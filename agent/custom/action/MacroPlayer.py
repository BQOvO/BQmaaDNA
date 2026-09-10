import json
import os
import random
import re
import time
import traceback
from maa.context import Context
from maa.custom_action import CustomAction


def resolve_macro_path(file_path: str) -> str:
    if os.path.isabs(file_path):
        return file_path
    _project_root = os.getcwd()
    return os.path.join(_project_root, "resource", "macros", file_path)


# ========== 默认值（摇杆参数写死在这里） ==========
DEFAULT_JOYSTICK_CENTER_X = 205
DEFAULT_JOYSTICK_CENTER_Y = 535
DEFAULT_MOVE_DISTANCE = 70
DEFAULT_MOVE_DURATION = 100

# fly / jump 默认坐标
DEFAULT_FLY_X = 1107
DEFAULT_FLY_Y = 360
DEFAULT_JUMP_X = 997
DEFAULT_JUMP_Y = 404

# ======== 8方向摇杆偏移 ========
DIRECTION_OFFSETS = {
    'up': (0, -1),
    'down': (0, 1),
    'left': (-1, 0),
    'right': (1, 0),
    'up_right': (0.707, -0.707),
    'up_left': (-0.707, -0.707),
    'down_right': (0.707, 0.707),
    'down_left': (-0.707, 0.707),
}
# =================================


def _get_click_pos(coord: tuple) -> tuple:
    """根据坐标参数返回实际点击位置。
    - (x, y) 精确坐标，直接返回
    - (x1, y1, x2, y2) 矩形范围，用 Beta(2,2) 分布随机取点，越靠近中心概率越高
    """
    if len(coord) == 2:
        return coord
    x1, y1, x2, y2 = coord
    min_x, max_x = min(x1, x2), max(x1, x2)
    min_y, max_y = min(y1, y2), max(y1, y2)
    rx = min_x + (max_x - min_x) * random.betavariate(2, 2)
    ry = min_y + (max_y - min_y) * random.betavariate(2, 2)
    return int(rx), int(ry)


def _split_params(params_str: str) -> list:
    """按逗号分割参数字符串，保留方括号内的逗号"""
    result = []
    depth = 0
    current = []
    for ch in params_str:
        if ch == '[':
            depth += 1
            current.append(ch)
        elif ch == ']':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            result.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        result.append(''.join(current))
    return result


def _parse_coord(coord_str: str) -> tuple:
    """解析 [x,y] 为 (x, y)"""
    nums = re.findall(r'-?\d+', coord_str)
    return tuple(int(n) for n in nums)


def parse_macro(text: str) -> list:
    """解析 .macro 格式文本，返回 step 列表"""
    text = re.sub(r'//.*', '', text)

    pattern = r'(\w+)\{([^}]*)\};'
    matches = re.findall(pattern, text)

    steps = []
    for action_name, params_str in matches:
        step = {
            'action': action_name,
            'positional': [],
            'wait': 0,
            'repeat': 1,
            'duration': 0,
            'end_hold': 0,
        }

        params = _split_params(params_str)
        for param in params:
            param = param.strip()
            if not param:
                continue
            if param.startswith('['):
                step['positional'].append(_parse_coord(param))
            elif param.startswith('wait('):
                m = re.search(r'wait\((\d+)\)', param)
                if m:
                    step['wait'] = int(m.group(1))
            elif param.startswith('repeat('):
                m = re.search(r'repeat\((\d+)\)', param)
                if m:
                    step['repeat'] = int(m.group(1))
            elif param.startswith('duration('):
                m = re.search(r'duration\((\d+)\)', param)
                if m:
                    step['duration'] = int(m.group(1))
            elif param.startswith('end_hold('):
                m = re.search(r'end_hold\((\d+)\)', param)
                if m:
                    step['end_hold'] = int(m.group(1))
            elif param.isdigit():
                step['positional'].append(int(param))

        steps.append(step)

    return steps


class MacroPlayer(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        print("[MacroPlayer] run() 开始执行", flush=True)
        try:
            param_str = argv.custom_action_param
            try:
                param = json.loads(param_str)
            except json.JSONDecodeError:
                param = param_str

            controller = context.tasker.controller

            # ---------- 情况1：纯字符串 = 文件路径 ----------
            if isinstance(param, str):
                resolved = resolve_macro_path(param)
                print(f"[MacroPlayer] 从文件读取宏: {resolved}", flush=True)
                with open(resolved, "r", encoding="utf-8") as f:
                    content = f.read()

                if param.endswith(".macro"):
                    steps = parse_macro(content)
                    self._execute_macro(steps, controller)
                else:
                    self._execute_json(content, controller)

            # ---------- 情况2：dict = 内联或 {"file": "..."} ----------
            elif isinstance(param, dict):
                steps = param.get("steps")
                macro_file = param.get("file")

                if steps is not None:
                    self._execute_json(json.dumps({"steps": steps}), controller)
                elif macro_file:
                    resolved = resolve_macro_path(macro_file)
                    print(f"[MacroPlayer] 从文件读取宏: {resolved}", flush=True)
                    with open(resolved, "r", encoding="utf-8") as f:
                        content = f.read()

                    if macro_file.endswith(".macro"):
                        steps = parse_macro(content)
                        self._execute_macro(steps, controller)
                    else:
                        self._execute_json(content, controller)
                else:
                    print("[MacroPlayer] 未指定 steps 或 file 参数", flush=True)
                    return CustomAction.RunResult(success=False)
            else:
                print("[MacroPlayer] 参数格式错误", flush=True)
                return CustomAction.RunResult(success=False)

            print("[MacroPlayer] 执行完成", flush=True)
            return CustomAction.RunResult(success=True)

        except Exception as e:
            print(f"[MacroPlayer] 未捕获的异常: {e}", flush=True)
            traceback.print_exc()
            return CustomAction.RunResult(success=False)

    # ==================== .macro 格式执行 ====================

    def _execute_macro(self, steps: list, controller) -> None:
        for step in steps:
            self._execute_new_step(step, controller)

    def _execute_new_step(self, step: dict, controller) -> None:
        action = step['action']
        wait_after = step['wait']
        repeat = step['repeat']
        positional = step['positional']
        duration = step['duration']
        end_hold = step.get('end_hold', 0)

        print(f"[MacroPlayer] 执行: {action} end_hold={end_hold}ms wait={wait_after}ms repeat={repeat}", flush=True)

        for _ in range(repeat):
            if action == 'fly':
                if positional:
                    x, y = _get_click_pos(positional[0])
                else:
                    x, y = DEFAULT_FLY_X, DEFAULT_FLY_Y
                controller.post_click(x, y).wait()

            elif action == 'jump':
                if positional:
                    x, y = _get_click_pos(positional[0])
                else:
                    x, y = DEFAULT_JUMP_X, DEFAULT_JUMP_Y
                controller.post_click(x, y).wait()

            elif action == 'click':
                x, y = _get_click_pos(positional[0])
                controller.post_click(x, y).wait()

            elif action == 'longpress':
                x, y = _get_click_pos(positional[0])
                controller.post_swipe(x, y, x, y, duration).wait()

            elif action == 'swipe':
                (x1, y1), (x2, y2) = _get_click_pos(positional[0]), _get_click_pos(positional[1])
                controller.post_swipe(x1, y1, x2, y2, duration).wait()

            elif action in ('up', 'down', 'left', 'right',
                            'up_right', 'up_left', 'down_right', 'down_left'):
                # 直接通过触摸事件控制虚拟摇杆（不依赖键盘映射）
                # touch_down(中心) → touch_move(目标方向) → sleep(hold) → touch_up
                cx, cy = DEFAULT_JOYSTICK_CENTER_X, DEFAULT_JOYSTICK_CENTER_Y
                move_dist = DEFAULT_MOVE_DISTANCE
                dx, dy = DIRECTION_OFFSETS[action]
                ex = int(cx + dx * move_dist)
                ey = int(cy + dy * move_dist)
                hold_ms = end_hold if end_hold > 0 else 100

                print(f"[MacroPlayer]   🎮 摇杆: ({cx},{cy})→({ex},{ey}) hold={hold_ms}ms", flush=True)

                controller.post_touch_down(cx, cy).wait()
                controller.post_touch_move(ex, ey).wait()
                time.sleep(hold_ms / 1000.0)
                controller.post_touch_up().wait()

            elif action == 'wait':
                dur = positional[0] if positional else 0
                time.sleep(dur / 1000.0)

            else:
                print(f"[MacroPlayer] 未知动作: {action}", flush=True)

            if wait_after > 0:
                time.sleep(wait_after / 1000.0)

    # ==================== 旧 .json 格式兼容 ====================

    def _execute_json(self, content: str, controller) -> None:
        macro = json.loads(content)
        if isinstance(macro, list):
            macro = {"steps": macro}

        start_time = time.perf_counter()
        abs_time = 0

        def execute_step(step):
            nonlocal abs_time
            delay = step.get("delay", 0)
            abs_time += delay
            target_time = start_time + abs_time / 1000.0
            now = time.perf_counter()
            if now < target_time:
                time.sleep(target_time - now)

            action = step["action"]
            if action == "click":
                x, y = step["x"], step["y"]
                repeat = step.get("repeat", 1)
                repeat_interval = step.get("repeat_interval", 0)
                for _ in range(repeat):
                    controller.post_click(x, y).wait()
                    if repeat_interval > 0:
                        time.sleep(repeat_interval / 1000.0)
            elif action == "fly":
                controller.post_click(DEFAULT_FLY_X, DEFAULT_FLY_Y).wait()
            elif action == "jump":
                controller.post_click(DEFAULT_JUMP_X, DEFAULT_JUMP_Y).wait()
            elif action == "long_press":
                x, y = step["x"], step["y"]
                duration = step.get("duration", 1000)
                controller.post_swipe(x, y, x, y, duration).wait()
            elif action == "swipe":
                x1, y1 = step["x1"], step["y1"]
                x2, y2 = step["x2"], step["y2"]
                move_dur = step.get("move_duration", 200)
                hold_dur = step.get("hold_duration", 0)
                controller.post_swipe(x1, y1, x2, y2, move_dur).wait()
                if hold_dur > 0:
                    time.sleep(hold_dur / 1000.0)
            elif action in ("up", "down", "left", "right",
                            "move_up", "move_down", "move_left", "move_right",
                            "up_right", "up_left", "down_right", "down_left"):
                center_x = step.get("center_x", DEFAULT_JOYSTICK_CENTER_X)
                center_y = step.get("center_y", DEFAULT_JOYSTICK_CENTER_Y)
                distance = step.get("distance", DEFAULT_MOVE_DISTANCE)
                duration = step.get("duration", DEFAULT_MOVE_DURATION)
                endhold = step.get("endhold", 0)
                if action in ("up", "move_up"):
                    end_x, end_y = center_x, center_y - distance
                elif action in ("down", "move_down"):
                    end_x, end_y = center_x, center_y + distance
                elif action in ("left", "move_left"):
                    end_x, end_y = center_x - distance, center_y
                elif action == "right":
                    end_x, end_y = center_x + distance, center_y
                elif action == "up_right":
                    end_x, end_y = center_x + int(distance * 0.707), center_y - int(distance * 0.707)
                elif action == "up_left":
                    end_x, end_y = center_x - int(distance * 0.707), center_y - int(distance * 0.707)
                elif action == "down_right":
                    end_x, end_y = center_x + int(distance * 0.707), center_y + int(distance * 0.707)
                elif action == "down_left":
                    end_x, end_y = center_x - int(distance * 0.707), center_y + int(distance * 0.707)
                else:
                    end_x, end_y = center_x + distance, center_y
                controller.post_swipe(center_x, center_y, end_x, end_y, duration, endhold).wait()
            elif action == "wait":
                dur = step.get("duration", 0)
                time.sleep(dur / 1000.0)
            elif action == "loop":
                count = step.get("count", 1)
                for _ in range(count):
                    for sub_step in step.get("steps", []):
                        execute_step(sub_step)
            else:
                print(f"[MacroPlayer] 未知动作: {action}", flush=True)

        for step in macro.get("steps", []):
            execute_step(step)


"""
===== MacroPlayer 功能说明 =====

宏播放器：执行预定义的宏操作序列。
支持两种格式：
  1. 新格式 .macro —— 简洁的自定义语法，推荐使用
  2. 旧格式 .json   —— 向后兼容

===== 新格式 .macro 语法 =====

文件后缀：.macro
外层用 {} 包裹，每个动作用 ; 结束，支持 // 注释。

  action{参数1, 参数2, ...};

参数类型：
  [x,y]         坐标（位置参数）
  wait(ms)      动作后等待（毫秒）
  repeat(n)     重复次数
  duration(ms)  持续时间（毫秒），用于 longpress 和 swipe

支持的动作：

  fly{wait(1000), repeat(3)}
  fly{[1107, 360], wait(1000), repeat(3)}   // 自定义坐标

  jump{wait(1000), repeat(3)}
  jump{[997, 404], wait(1000), repeat(3)}   // 自定义坐标

  click{[x, y], wait(1000), repeat(3)}

  longpress{[x, y], duration(1000), wait(1000), repeat(3)}

  swipe{[x1, y1], [x2, y2], duration(200), wait(1000), repeat(3)}

  up{wait(1000), repeat(3)}
  down{wait(1000), repeat(3)}
  left{wait(1000), repeat(3)}
  right{wait(1000), repeat(3)}

  wait{1000}                                // 单独等待

===== 旧格式 .json 兼容 =====

仍支持原有的 JSON 格式，包括 delay / steps / file 等字段。
旧格式中的 long_press、move_up/down/left/right 别名继续有效。

===== Pipeline 用法 =====

{
    "执行宏": {
        "recognition": "DirectHit",
        "action": "Custom",
        "custom_action": "MacroPlayer",
        "custom_action_param": "夜航手册60/夜航手册60.macro"
    }
}
"""