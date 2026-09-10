"""
MacroRecorder — PC端键盘录制 + 回放

录制: 监听键盘 WASD → 8方向 → 保存为 .macro 文件
回放: 读取 .macro → ADB 虚拟摇杆 Swipe 复现

用法:
  录制: MacroRecorderStart
  回放: MacroPlayer (已有)
  停止: MacroRecorderStop (手动停止)
"""

import json
import math
import os
import threading
import time
import traceback

from maa.context import Context
from maa.custom_action import CustomAction

try:
    from pynput import keyboard as pynput_keyboard

    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False

# ========== 默认参数 ==========
DEFAULT_IDLE_TIMEOUT = 5.0  # 空闲超时秒数
DEFAULT_MAX_DURATION = 300.0  # 最大录制时长
DEFAULT_OUTPUT_FILE = "recorded.macro"

# ========== 录制状态 ==========
_recording = False
_recorded_steps = []  # [{"direction": "up", "duration": 0.35}, ...]
_idle_timeout = DEFAULT_IDLE_TIMEOUT
_max_duration = DEFAULT_MAX_DURATION
_output_file = DEFAULT_OUTPUT_FILE
_stop_event = threading.Event()
_listener = None

# 键盘状态
_pressed_keys = set()
_last_direction = None
_direction_start = 0.0
_last_any_key_time = 0.0


# ========== 键盘监听 ==========

def _keys_to_direction(keys: set) -> str | None:
    """WASD 组合 → 8方向"""
    w = "w" in keys or "W" in keys
    s = "s" in keys or "S" in keys
    a = "a" in keys or "A" in keys
    d = "d" in keys or "D" in keys

    if w and d:
        return "up_right"
    if w and a:
        return "up_left"
    if s and d:
        return "down_right"
    if s and a:
        return "down_left"
    if w:
        return "up"
    if s:
        return "down"
    if a:
        return "left"
    if d:
        return "right"
    return None


def _on_press(key):
    global _pressed_keys, _last_any_key_time, _recording

    try:
        if hasattr(key, "char") and key.char:
            _pressed_keys.add(key.char)
            _last_any_key_time = time.time()
        elif key == pynput_keyboard.Key.esc:
            print("\n[MacroRecorder] 🛑 ESC 按下，停止录制", flush=True)
            _stop_recording()
    except Exception:
        pass


def _on_release(key):
    global _pressed_keys, _last_any_key_time

    try:
        if hasattr(key, "char") and key.char:
            _pressed_keys.discard(key.char)
            _last_any_key_time = time.time()
    except Exception:
        pass


def _record_loop():
    """录制主循环 — 在后台线程运行"""
    global _recording, _recorded_steps, _last_direction, _direction_start

    _recorded_steps = []
    _last_direction = None
    _direction_start = time.time()
    _last_any_key_time = time.time()

    print("[MacroRecorder] ▶ 录制已开始！用 WASD 推摇杆走副本", flush=True)
    print("[MacroRecorder]    W=上 S=下 A=左 D=右  松开键盘5秒自动停止", flush=True)

    start_time = time.time()

    while _recording and not _stop_event.is_set():
        now = time.time()

        # 超时检查
        if now - start_time > _max_duration:
            print(f"[MacroRecorder] ⏰ 达到最大录制时长 {_max_duration}s", flush=True)
            break

        # 空闲超时检查 (0 或负数 = 禁用)
        if _idle_timeout > 0 and now - _last_any_key_time > _idle_timeout:
            idle = now - _last_any_key_time
            print(f"[MacroRecorder] ⏰ 空闲 {idle:.1f}s，自动停止", flush=True)
            break

        # 计算当前方向
        current_dir = _keys_to_direction(_pressed_keys)

        if current_dir != _last_direction:
            # 方向变化：先保存上一段
            if _last_direction is not None:
                duration = now - _direction_start
                if duration >= 0.05:  # 忽略极短按键
                    _recorded_steps.append({"direction": _last_direction, "duration": round(duration, 3)})
                    print(
                        f"[MacroRecorder] 📝 #{len(_recorded_steps)} "
                        f"方向={_last_direction} 持续={duration:.2f}s",
                        flush=True,
                    )

            # 开始新方向
            _last_direction = current_dir
            _direction_start = now

            if current_dir:
                print(f"[MacroRecorder] 🎮 {current_dir} →", end=" ", flush=True)

        time.sleep(0.05)  # 50ms 采样

    # 保存最后一段
    if _last_direction is not None:
        duration = time.time() - _direction_start
        if duration >= 0.05:
            _recorded_steps.append({"direction": _last_direction, "duration": round(duration, 3)})
            print(
                f"[MacroRecorder] 📝 #{len(_recorded_steps)} "
                f"方向={_last_direction} 持续={duration:.2f}s",
                flush=True,
            )

    _recording = False

    # 保存到文件
    _save_macro()


def _save_macro():
    """保存录制的步骤到 .macro 文件"""
    macro_path = os.path.join("resource", "macros", _output_file)

    if not _recorded_steps:
        print("[MacroRecorder] ⚠ 没有录制到任何步骤", flush=True)
        return

    lines = []
    for step in _recorded_steps:
        d = step["direction"]
        dur = int(step["duration"] * 1000)
        if dur > 0:
            lines.append(f'{d}{{end_hold({dur}), wait(500)}};')
        else:
            lines.append(f'{d}{{wait(500)}};')

    os.makedirs(os.path.dirname(macro_path), exist_ok=True)
    with open(macro_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    total_dur = sum(s["duration"] for s in _recorded_steps)
    print(
        f"[MacroRecorder] 💾 宏已保存: {macro_path} "
        f"({len(_recorded_steps)} 步, 总时长 {total_dur:.1f}s)",
        flush=True,
    )


def _start_recording():
    global _recording, _listener, _stop_event, _pressed_keys
    global _last_direction, _direction_start, _last_any_key_time

    if _recording:
        return

    _stop_event.clear()
    _pressed_keys = set()
    _last_direction = None
    _direction_start = 0.0
    _last_any_key_time = time.time()
    _recording = True

    # 启动键盘监听
    _listener = pynput_keyboard.Listener(on_press=_on_press, on_release=_on_release)
    _listener.start()

    # 启动录制循环
    t = threading.Thread(target=_record_loop, daemon=True)
    t.start()


def _stop_recording():
    global _recording
    _recording = False
    _stop_event.set()
    if _listener:
        _listener.stop()


# =====================================================================
#  MacroRecorderStart
# =====================================================================


class MacroRecorderStart(CustomAction):
    """
    启动录制。监听键盘 WASD，自动记录方向和持续时间。

    参数:
      idle_timeout: 空闲超时秒数 (默认 5.0)
      max_duration: 最大录制时长 (默认 300.0)
      output_file: 输出文件名 (默认 "recorded.macro")
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        global _recording, _idle_timeout, _max_duration, _output_file

        try:
            if not HAS_PYNPUT:
                print("[MacroRecorderStart] ❌ pynput 未安装！请运行: pip install pynput", flush=True)
                return CustomAction.RunResult(success=False)

            if _recording:
                print("[MacroRecorderStart] ⚠ 已在录制中", flush=True)
                return CustomAction.RunResult(success=True)

            param_str = argv.custom_action_param
            if param_str:
                try:
                    param = json.loads(param_str)
                except (json.JSONDecodeError, TypeError):
                    param = {}
            else:
                param = {}

            _idle_timeout = param.get("idle_timeout", DEFAULT_IDLE_TIMEOUT)
            _max_duration = param.get("max_duration", DEFAULT_MAX_DURATION)
            _output_file = param.get("output_file", DEFAULT_OUTPUT_FILE)

            _start_recording()

            print(
                f"[MacroRecorderStart] 空闲超时={_idle_timeout}s "
                f"最大时长={_max_duration}s",
                flush=True,
            )

            # 阻塞等待录制结束
            while _recording:
                time.sleep(0.5)

            print("[MacroRecorderStart] ✅ 录制已结束", flush=True)
            return CustomAction.RunResult(success=True)

        except Exception as e:
            print(f"[MacroRecorderStart] ❌ {e}", flush=True)
            traceback.print_exc()
            return CustomAction.RunResult(success=False)


# =====================================================================
#  MacroRecorderStop — 手动停止录制
# =====================================================================


class MacroRecorderStop(CustomAction):
    """
    手动停止录制并保存。

    参数:
      file: 输出文件名 (可选，覆盖 Start 时的设置)
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        global _recording, _output_file

        try:
            if not _recording:
                print("[MacroRecorderStop] ⚠ 没有在录制", flush=True)
                return CustomAction.RunResult(success=True)

            param_str = argv.custom_action_param
            if param_str:
                try:
                    param = json.loads(param_str)
                except (json.JSONDecodeError, TypeError):
                    param = {}
            else:
                param = {}

            if "file" in param:
                _output_file = param["file"]

            _stop_recording()

            print(f"[MacroRecorderStop] 🛑 录制已停止，保存到 {_output_file}", flush=True)
            return CustomAction.RunResult(success=True)

        except Exception as e:
            print(f"[MacroRecorderStop] ❌ {e}", flush=True)
            traceback.print_exc()
            return CustomAction.RunResult(success=False)