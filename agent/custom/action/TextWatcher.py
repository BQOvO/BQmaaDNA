import json
import time
import traceback
from maa.context import Context
from maa.custom_action import CustomAction


class TextWatcher(CustomAction):
    CONFIGS = {
        1: {
            "expected": "委托完成",
            "roi": [18, 412, 142, 54],
        },
        2: {
            "expected": "行动抉择",
            "roi": [592,8,99,30],
        },
    }

    DEFAULT_TIMEOUT = 3.0
    INTERVAL = 0.5

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        except json.JSONDecodeError:
            print("[TextWatcher] 参数 JSON 解析失败")
            return CustomAction.RunResult(success=False)

        config_id = param.get("config", None)
        expected = param.get("expected", None)
        roi = param.get("roi", None)

        node_data = context.get_node_data(argv.node_name)
        timeout = self.DEFAULT_TIMEOUT
        if node_data and isinstance(node_data, dict):
            raw = node_data.get("time", None)
            if raw is not None:
                try:
                    timeout = float(raw)
                except (ValueError, TypeError):
                    pass

        if config_id is not None:
            preset = self.CONFIGS.get(config_id)
            if preset is None:
                print(f"[TextWatcher] 未知预设配置 config={config_id}")
                return CustomAction.RunResult(success=False)
            expected = expected or preset["expected"]
            roi = roi or preset.get("roi", None)

        if not expected:
            print("[TextWatcher] 未指定 expected 文本")
            return CustomAction.RunResult(success=False)

        print(f"[TextWatcher] 开始监视文本: \"{expected}\", ROI={roi}, timeout={timeout}s")

        start_time = time.monotonic()
        while True:
            if context.tasker.stopping:
                print("[TextWatcher] 任务被停止")
                return CustomAction.RunResult(success=False)

            image = self._screencap(context)
            if image is None:
                return CustomAction.RunResult(success=False)

            if self._ocr_hit(context, image, roi, expected):
                elapsed = time.monotonic() - start_time
                print(f"[TextWatcher] 检测到 \"{expected}\", 耗时 {elapsed:.1f}s")
                return CustomAction.RunResult(success=True)

            if time.monotonic() - start_time >= timeout:
                print(f"[TextWatcher] 超时 ({timeout}s), 未检测到 \"{expected}\"")
                return CustomAction.RunResult(success=False)

            time.sleep(self.INTERVAL)

    def _screencap(self, context):
        try:
            image = context.tasker.controller.post_screencap().wait().get()
            if image is None:
                print("[TextWatcher] 截图为空")
            return image
        except Exception as e:
            print(f"[TextWatcher] 截图失败: {e}")
            traceback.print_exc()
            return None

    def _ocr_hit(self, context, image, roi, pattern):
        entry = "_tw_check"
        node = {
            "recognition": "OCR",
            "expected": [pattern],
        }
        if roi:
            node["roi"] = roi

        pipeline = {entry: node}
        try:
            reco = context.run_recognition(entry, image, pipeline)
            return reco.hit if reco else False
        except Exception:
            return False


"""
===== TextWatcher 功能说明 =====

文本监视器：在指定时间内轮询 OCR 检测目标文本是否出现。
检测到 → success=True → Pipeline 走 next
超时未检测到 → success=False → Pipeline 走 on_error

===== 核心实现 =====

1. 预设配置 (CONFIGS)：类级别字典，key 为 config 编号，value 包含 expected 和 roi。
   config 只影响"找什么"和"在哪找"，不影响 timeout。

2. 轮询循环：每隔 INTERVAL 秒截图 → OCR 检测 expected 文本 → 命中则返回成功。

3. 参数分离设计（关键）：
   - config 从 custom_action_param 读取（副本选择 option 通过 pipeline_override 注入）
   - timeout 从节点自定义 time 字段读取（技能释放间隔 option 通过 pipeline_override 注入）
   - 两个 option 覆盖不同字段，互不冲突，MaaFramework 自动合并

===== 使用教程 =====

用法1：纯预设（最简洁）
  custom_action_param: {"config": 1}
  timeout 取自节点 time 字段（默认 10s），interval 默认 0.5s。

用法2：预设 + 节点 time 字段
  custom_action_param: {"config": 1}
  time: 20  （节点 time 字段，单位秒）

用法3：完全自定义（不传 config）
  custom_action_param: {"expected": "开始挑战", "roi": [639, 469, 294, 54]}
  time: 5  （节点 time 字段，单位秒）

===== Pipeline JSON 示例 =====

{
    "检测是否完成委托": {
        "recognition": "DirectHit",
        "action": "Custom",
        "custom_action": "TextWatcher",
        "custom_action_param": {"config": 1},
        "time": 10,
        "next": ["结束计时"],
        "on_error": ["超时处理"]
    }
}

===== Option 联动示例 =====

副本选择 option → 覆盖 custom_action_param.config:
  "pipeline_override": {
      "夜航20-TextWatcher": {"custom_action_param": {"config": 2}}
  }

技能释放间隔 option → 覆盖 time:
  "pipeline_override": {
      "夜航20-TextWatcher": {"time": "{黎瑟技能释放间隔}"}
  }

两个 option 覆盖不同字段，MaaFramework 自动合并，互不冲突。

===== 如何添加新预设 =====

在 CONFIGS 字典中新增条目即可：
CONFIGS = {
    1: {"expected": "委托完成", "roi": [18, 412, 142, 54]},
    2: {"expected": "行动抉择", "roi": [0, 0, 1280, 720]},
    3: {"expected": "再次进行", "roi": [764, 619, 508, 90]},
}
Pipeline 中只需 {"config": 2} 即可使用。
"""