import json
import re
import time
import traceback
from maa.context import Context
from maa.custom_action import CustomAction


class NumberComparator(CustomAction):
    ROI = [933,407,81,24]
    BTN_DECREASE = [965, 475]
    BTN_INCREASE = [1180, 475]
    CLICK_WHEN_ONE = [1206,384]
    CLICK_OPEN = [1200, 480]
    MAX_RETRIES = 100
    ENABLE_ADJUST = False

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        except json.JSONDecodeError:
            print("[NumberComparator] 参数 JSON 解析失败")
            return CustomAction.RunResult(success=False)

        target = int(param.get("target", 1))
        enable_adjust = param.get("enable_adjust", self.ENABLE_ADJUST)

        image = self._screencap(context)
        if image is None:
            return CustomAction.RunResult(success=False)

        has_text = self._ocr_hit(context, image, self.ROI, "\\d+/\\d+")

        if target == 1:
            if has_text:
                self._click(context, self.CLICK_WHEN_ONE, "target=1, 检测到文字, 点击取消自动轮次")
                return CustomAction.RunResult(success=True)
            else:
                print("[NumberComparator] target=1, 未检测到文字, 跳过点击, 继续执行 next")
                return CustomAction.RunResult(success=True)

        if not enable_adjust:
            print(f"[NumberComparator] ENABLE_ADJUST=False, 仅 target=1 生效, 当前 target={target}, 跳过调整")
            return CustomAction.RunResult(success=True)

        if not has_text:
            self._click(context, self.CLICK_OPEN, "未检测到轮次文字, 点击开启自动轮次")
            time.sleep(0.5)

        for attempt in range(1, self.MAX_RETRIES + 1):
            time.sleep(0.3)
            image = self._screencap(context)
            if image is None:
                return CustomAction.RunResult(success=False)

            has_text = self._ocr_hit(context, image, self.ROI, "\\d+/\\d+")
            if not has_text:
                self._click(context, self.CLICK_OPEN, "轮次文字消失, 点击开启自动轮次")
                continue

            recognized = self._get_number(context, image)
            if recognized is None:
                print(f"[NumberComparator] 第{attempt}次 OCR 未能识别数字, 继续重试")
                continue

            print(f"[NumberComparator] 第{attempt}次 识别={recognized}, 目标={target-1}, 比较: {recognized}+1 vs {target}")
            if recognized + 1 == target:
                print(f"[NumberComparator] {recognized}+1 == {target}, 成功!")
                return CustomAction.RunResult(success=True)
            elif recognized + 1 > target:
                self._click(context, self.BTN_DECREASE, f"{recognized}+1 > {target}, 点击调小")
            else:
                self._click(context, self.BTN_INCREASE, f"{recognized}+1 < {target}, 点击调大")

        print(f"[NumberComparator] 已达最大重试次数 {self.MAX_RETRIES}, 返回失败")
        return CustomAction.RunResult(success=False)

    def _click(self, context, pos, msg):
        if pos and len(pos) >= 2:
            x, y = pos[0], pos[1]
            print(f"[NumberComparator] {msg} ({x}, {y})")
            context.tasker.controller.post_click(x, y).wait()

    def _screencap(self, context):
        try:
            image = context.tasker.controller.post_screencap().wait().get()
            if image is None:
                print("[NumberComparator] 截图为空")
            return image
        except Exception as e:
            print(f"[NumberComparator] 截图失败: {e}")
            traceback.print_exc()
            return None

    def _ocr_hit(self, context, image, roi, pattern):
        entry = "_nc_presence"
        pipeline = {
            entry: {
                "recognition": "OCR",
                "roi": roi,
                "expected": [pattern],
            }
        }
        try:
            reco = context.run_recognition(entry, image, pipeline)
            return reco.hit if reco else False
        except Exception:
            return False

    def _get_number(self, context, image):
        entry = "_nc_getnum"
        pipeline = {
            entry: {
                "recognition": "OCR",
                "roi": self.ROI,
                "expected": ["\\d+/\\d+"],
            }
        }
        try:
            reco = context.run_recognition(entry, image, pipeline)
            if reco and reco.hit and reco.best_result:
                best = reco.best_result
                if hasattr(best, "text"):
                    text = best.text
                else:
                    return None
                match = re.search(r"(\d+)/", text)
                if match:
                    return int(match.group(1))
            return None
        except Exception:
            return None


"""
===== NumberComparator 功能说明 =====

轮次数字比较器：OCR 识别画面中 "N/M" 格式的轮次数字，通过点击按钮调整到目标值。
常用于自动轮次设置场景。

===== 核心实现 =====

1. OCR 识别：在 ROI=[1030,428,86,28] 区域内检测 "\\d+/\\d+" 格式的文字。
2. target=1 特殊处理：检测到文字则点击取消自动轮次；未检测到则跳过（说明已经是手动模式）。
3. 数字扫描：从 target-1 开始向两侧扩展扫描，找到匹配的数字后比较大小。
4. 逐次逼近：recognized+1 > target → 点击调小；recognized+1 < target → 点击调大。
5. 最多重试 MAX_RETRIES=200 次，每次间隔 0.3s。

===== 使用教程 =====

参数格式：
{
    "target": 5    // 目标轮次，如 target=5 表示要调到第 4 轮
}

===== Pipeline JSON 示例 =====

{
    "轮次比较": {
        "recognition": "DirectHit",
        "action": "Custom",
        "custom_action": "NumberComparator",
        "custom_action_param": {"target": 5},
        "next": ["下一步"]
    }
}

===== 类常量配置 =====

ROI          = [1030, 428, 86, 28]   // 数字显示区域
BTN_DECREASE = [965, 475]            // 调小按钮 (X=965, 坐标小)
BTN_INCREASE = [1180, 475]           // 调大按钮 (X=1180, 坐标大)
CLICK_WHEN_ONE = [1210, 416]         // target=1 时的取消按钮
CLICK_OPEN   = [1200, 480]           // 开启自动轮次按钮
MAX_RETRIES  = 200                   // 最大重试次数
"""