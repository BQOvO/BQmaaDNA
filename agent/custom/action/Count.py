"""
===== Count 计数器系统 - 完整使用说明 =====

提供 7 个 Action，覆盖计数的原子操作。

─── 旧版（保留兼容）─────────────────────────────
  Count         +1 后比较，达标走 next         {"id":"round","target":5}
  CountReset    归零                           "round"
  CountPrint    输出到 UI                      "第{round}/{round_target}轮"
  CountCleanup  清理计数器                     {"id":"round"}

─── 新版（推荐，原子操作）────────────────────────
  CountAdd      纯加法，不比较                  {"id":"round","step":1}
  CountCheck    纯比较，不修改                  {"id":"round","target":5}
  CountSet      设值，不比较                    {"id":"round","value":0}

═══ CountAdd ======================================================

  参数:
    {"id":"round","step":1}              基础用法，+1（step 默认 1）
    {"id":"round","step":5}              每次 +5
    {"id":"round","step":1,"target":5}   同时注册 target，CountPrint 可立即拿到 {id_target}

  验证:
    step 必须为正整数，传 0 或负数会报错并返回 False

  ⚠️ option 同步:
    如果在 CountAdd 中传了 target，且该 target 受 UI option 控制，
    则 pipeline_override 必须同时覆盖 CountAdd 和 CountCheck 两个节点。
    如不传 target 则只需覆盖 CountCheck。

═══ CountCheck ====================================================

  参数:
    {"id":"round","target":5}                检查 >= 5
    {"id":"round","target":5,"auto_reset":true}  达标后立即归零
    {"id":"round"}                           打印警告，永远返回 False

  比较逻辑:
    current >= target  → 达标，success=True，走 next
    current < target   → 未达标，success=False，走 on_error

  注: target 在 CountCheck 执行时自动注册到 _targets，
      此后 CountPrint 可通过 {id_target} 读取。

═══ CountSet =======================================================

  参数:
    {"id":"round","value":3}    设为 3
    {"id":"round","value":0}    归零（等同 CountReset 但不输出 UI 日志）

  验证:
    value 必须为非负整数

═══ CountPrint =====================================================

  模板变量:
    {id}        当前值，如 {round} → 3
    {id_target} 目标值，如 {round_target} → 5，未注册时显示 "∞"

  参数:
    "第{round}/{round_target}轮"             字符串模式
    {"msg":"第{round}/{round_target}轮"}     字典模式

═══ 典型场景：轮次嵌套把数 ==========================================

  Pipeline JSON:

    {
      "战斗-轮次+1": {
        "custom_action": "CountAdd",
        "custom_action_param": {"id":"round","step":1,"target":5},
        "next": ["战斗-轮次检查"]
      },
      "战斗-轮次检查": {
        "custom_action": "CountCheck",
        "custom_action_param": {"id":"round","target":5},
        "next": ["战斗-这把结束"],
        "on_error": ["战斗-轮次输出", "战斗-点击再次进行"]
      },
      "战斗-轮次输出": {
        "custom_action": "CountPrint",
        "custom_action_param": "第{round}/{round_target}轮"
      },
      "战斗-这把结束": {
        "custom_action": "CountSet",
        "custom_action_param": {"id":"round","value":0},
        "next": ["战斗-把数+1"]
      },
      "战斗-把数+1": {
        "custom_action": "CountAdd",
        "custom_action_param": {"id":"battle","step":1,"target":3},
        "next": ["战斗-把数检查"]
      },
      "战斗-把数检查": {
        "custom_action": "CountCheck",
        "custom_action_param": {"id":"battle","target":3},
        "next": ["全部完成"],
        "on_error": ["下一把流程"]
      }
    }

  执行流程:

    检测结算 → CountAdd(round+1) → CountCheck(round>=5?)
      ├─ 未达标 → CountPrint("第2/5轮") → 点"再次进行" → 继续打
      └─ 达标   → CountSet(round=0) → CountAdd(battle+1) → CountCheck(battle>=3?)
                    ├─ 未达标 → 下一把
                    └─ 达标   → 全部完成

  ⚠️ option 同步写法:

    如果 target 受 UI option 控制，pipeline_override 需覆盖两个节点:

    "pipeline_override": {
      "战斗-轮次检查": {
        "custom_action_param": {"id":"round","target":"{刷取轮次}"}
      },
      "战斗-轮次+1": {
        "custom_action_param": {"id":"round","step":1,"target":"{刷取轮次}"}
      }
    }
"""

import json
import threading
from collections import defaultdict
from maa.custom_action import CustomAction
from maa.context import Context
from ..utils.Logger import Logger

_globals = {}   # {task_id: {"count_xxx": int}}
_targets = {}   # {task_id: {"target_xxx": int}}
_reached = {}   # {task_id: {"count_xxx": bool}}
_lock = threading.RLock()


def _build_vars(task_id):
    """构建模板变量。每个计数器生成 {id} 和 {id_target} 两个变量。
    无 target 时 {id_target} = "∞"。
    """
    vars_dict = {}
    with _lock:
        tid_globals = _globals.get(task_id, {})
        for key, val in tid_globals.items():
            if key.startswith("count_"):
                cid = key[6:]
                vars_dict[cid] = val

        tid_targets = _targets.get(task_id, {})
        for key, val in tid_targets.items():
            if key.startswith("target_"):
                cid = key[7:]
                vars_dict[f"{cid}_target"] = val if val > 0 else "∞"

    return vars_dict


class Count(CustomAction):
    """计数器：每次调用 +1。配了 target 达标时 success=True 走 next，否则走 on_error。

    参数：
      字符串: "all"                   → 无限计数
      字典:   {"id":"all","target":10} → 计数到10达标
              {"id":"all","target":10,"auto_reset":false} → 达标后不自动归零
    """
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        except Exception:
            param = argv.custom_action_param

        if isinstance(param, str):
            cid = param
            target = 0
            auto_reset = True
        elif isinstance(param, dict):
            cid = param.get("id")
            target = param.get("target", 0)
            auto_reset = param.get("auto_reset", True)
        else:
            print("[Count] 参数格式错误，应为字符串或对象")
            return CustomAction.RunResult(success=False)

        if not cid:
            print("[Count] 缺少 id")
            return CustomAction.RunResult(success=False)

        task_id = argv.task_detail.task_id
        count_key = f"count_{cid}"
        target_key = f"target_{cid}"

        with _lock:
            tid_targets = _targets.setdefault(task_id, {})
            if target_key not in tid_targets:
                tid_targets[target_key] = target

            tid_globals = _globals.setdefault(task_id, {})
            tid_reached = _reached.setdefault(task_id, {})

            if auto_reset and tid_reached.get(count_key, False):
                tid_globals[count_key] = 0
                tid_reached[count_key] = False

            total = tid_globals.get(count_key, 0) + 1
            tid_globals[count_key] = total

        reached = (target > 0 and total >= target)
        if reached and auto_reset:
            with _lock:
                _reached.setdefault(task_id, {})[count_key] = True

        return CustomAction.RunResult(success=reached)


class CountReset(CustomAction):
    """重置计数器归零，保留 target 设置。

    参数：
      字符串: "all"
      字典:   {"id":"all"}
    """
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        except Exception:
            param = argv.custom_action_param

        if isinstance(param, str):
            cid = param
        elif isinstance(param, dict):
            cid = param.get("id")
        else:
            print("[CountReset] 参数格式错误")
            return CustomAction.RunResult(success=False)

        if not cid:
            print("[CountReset] 缺少 id")
            return CustomAction.RunResult(success=False)

        task_id = argv.task_detail.task_id
        count_key = f"count_{cid}"

        with _lock:
            tid_globals = _globals.setdefault(task_id, {})
            tid_globals[count_key] = 0
            tid_reached = _reached.get(task_id, {})
            tid_reached.pop(count_key, None)

        logger = Logger("CountReset", context)
        logger.ui(f"{cid} → 0", color="cyan")

        return CustomAction.RunResult(success=True)


class CountPrint(CustomAction):
    """输出计数器信息到 UI。

    参数：
      字符串: "第{all}次 成功{success} 失败{failed} 目标{all_target}"
      字典:   {"msg":"第{all}次 成功{success} 失败{failed} 目标{all_target}"}

    模板变量：{id} 和 {id_target}（所有已注册的计数器）。
    """
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        except Exception:
            param = argv.custom_action_param

        if isinstance(param, str):
            msg = param
        elif isinstance(param, dict):
            msg = param.get("msg", "")
        else:
            msg = ""

        task_id = argv.task_detail.task_id

        with _lock:
            vars_dict = _build_vars(task_id)

        if msg:
            safe_vars = defaultdict(lambda: 0, vars_dict)
            output = msg.format_map(safe_vars)
        else:
            parts = []
            for key, val in vars_dict.items():
                if not key.endswith("_target"):
                    parts.append(f"{key}: {val}")
            output = " | ".join(parts) if parts else ""

        logger = Logger("CountPrint", context)
        if output:
            print(output)
            logger.ui(output)
        else:
            logger.ui("[CountPrint] 没有可输出的统计信息", color="gray")

        return CustomAction.RunResult(success=True)


class CountCleanup(CustomAction):
    """清理计数器数据。

    参数：
      不传参: {}
      字符串: "all"                     → 清理指定id
      字典:   {"id":"all"}              → 清理指定id
              {"id":"all","keep_target":true} → 仅归零，保留target
    """
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        task_id = argv.task_detail.task_id

        try:
            param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        except Exception:
            param = {}

        if isinstance(param, str):
            cid = param
            keep_target = False
        elif isinstance(param, dict):
            cid = param.get("id")
            keep_target = param.get("keep_target", False)
        else:
            cid = None
            keep_target = False

        with _lock:
            if cid:
                count_key = f"count_{cid}"
                target_key = f"target_{cid}"
                tid_globals = _globals.get(task_id, {})
                tid_globals.pop(count_key, None)
                if not tid_globals:
                    _globals.pop(task_id, None)
                tid_reached = _reached.get(task_id, {})
                tid_reached.pop(count_key, None)
                if not tid_reached:
                    _reached.pop(task_id, None)
                if not keep_target:
                    tid_targets = _targets.get(task_id, {})
                    tid_targets.pop(target_key, None)
                    if not tid_targets:
                        _targets.pop(task_id, None)
                logger = Logger("CountCleanup", context)
                logger.ui(f"已清理 {cid}", color="gray")
            else:
                tid_globals = _globals.pop(task_id, {})
                glob_count = len(tid_globals)
                _reached.pop(task_id, None)
                if not keep_target:
                    _targets.pop(task_id, None)
                if glob_count > 0:
                    logger = Logger("CountCleanup", context)
                    logger.ui(f"已清理全部 {glob_count} 个计数器", color="gray")

        return CustomAction.RunResult(success=True)


class CountAdd(CustomAction):
    """纯加法：给计数器加 step，不比较。

    参数：
      {"id":"round","step":1}              → 基础用法，+1
      {"id":"round","step":5}              → 每次 +5
      {"id":"round","step":1,"target":5}   → 同时注册 target，CountPrint 可立即拿到 {id_target}

    ⚠️ option 同步注意：
      如果在 CountAdd 中传了 target，且该 target 受 UI option 控制，
      则 pipeline_override 必须同时覆盖 CountAdd 和 CountCheck 两个节点，
      否则 target 值可能不同步。如不传 target 则只需覆盖 CountCheck。
    """
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        except Exception:
            param = argv.custom_action_param

        if isinstance(param, str):
            cid = param
            step = 1
            reg_target = 0
        elif isinstance(param, dict):
            cid = param.get("id")
            step = param.get("step", 1)
            reg_target = param.get("target", 0)
        else:
            print("[CountAdd] 参数格式错误，应为字符串或对象")
            return CustomAction.RunResult(success=False)

        if not cid:
            print("[CountAdd] 缺少 id")
            return CustomAction.RunResult(success=False)

        if not isinstance(step, int) or step <= 0:
            print(f"[CountAdd] step 必须为正整数，收到: {step}")
            return CustomAction.RunResult(success=False)

        task_id = argv.task_detail.task_id
        count_key = f"count_{cid}"

        with _lock:
            tid_globals = _globals.setdefault(task_id, {})
            tid_globals[count_key] = tid_globals.get(count_key, 0) + step

            if reg_target > 0:
                target_key = f"target_{cid}"
                tid_targets = _targets.setdefault(task_id, {})
                tid_targets[target_key] = reg_target

        return CustomAction.RunResult(success=True)


class CountCheck(CustomAction):
    """纯比较：检查计数器是否 >= target，不修改计数器。

    参数：
      {"id":"round","target":5}                → 检查 round >= 5
      {"id":"round","target":5,"auto_reset":true} → 达标后立即归零
      {"id":"round"}                           → 打印警告，永远返回 False

    达标 → success=True 走 next
    未达标 → success=False 走 on_error
    """
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        except Exception:
            param = argv.custom_action_param

        if isinstance(param, str):
            cid = param
            target = 0
            auto_reset = False
        elif isinstance(param, dict):
            cid = param.get("id")
            target = param.get("target", 0)
            auto_reset = param.get("auto_reset", False)
        else:
            print("[CountCheck] 参数格式错误，应为字符串或对象")
            return CustomAction.RunResult(success=False)

        if not cid:
            print("[CountCheck] 缺少 id")
            return CustomAction.RunResult(success=False)

        if target <= 0:
            print(f"[CountCheck] target 未设置或无效 ({target})，永远返回 False")
            return CustomAction.RunResult(success=False)

        task_id = argv.task_detail.task_id
        count_key = f"count_{cid}"
        target_key = f"target_{cid}"

        with _lock:
            tid_targets = _targets.setdefault(task_id, {})
            tid_targets[target_key] = target

            tid_globals = _globals.setdefault(task_id, {})
            current = tid_globals.get(count_key, 0)

        reached = current >= target

        if reached and auto_reset:
            with _lock:
                tid_globals = _globals.setdefault(task_id, {})
                tid_globals[count_key] = 0

        return CustomAction.RunResult(success=reached)


class CountSet(CustomAction):
    """设值：直接设置计数器的值，不比较。

    参数：
      {"id":"round","value":3}    → 设为 3
      {"id":"round","value":0}    → 归零（等同 CountReset 但不输出 UI 日志）

    永远返回 success=True。
    """
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        except Exception:
            param = argv.custom_action_param

        if isinstance(param, str):
            cid = param
            value = 0
        elif isinstance(param, dict):
            cid = param.get("id")
            value = param.get("value", 0)
        else:
            print("[CountSet] 参数格式错误，应为字符串或对象")
            return CustomAction.RunResult(success=False)

        if not cid:
            print("[CountSet] 缺少 id")
            return CustomAction.RunResult(success=False)

        if not isinstance(value, int) or value < 0:
            print(f"[CountSet] value 必须为非负整数，收到: {value}")
            return CustomAction.RunResult(success=False)

        task_id = argv.task_detail.task_id
        count_key = f"count_{cid}"

        with _lock:
            tid_globals = _globals.setdefault(task_id, {})
            tid_globals[count_key] = value

            tid_reached = _reached.get(task_id, {})
            tid_reached.pop(count_key, None)

        return CustomAction.RunResult(success=True)