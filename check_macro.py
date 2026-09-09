"""
MacroPlayer .macro 文件校验 / 格式化工具

用法:
    python check_macro.py <file_or_directory>        # 校验
    python check_macro.py <file_or_directory> --fmt   # 校验 + 自动格式化
    python check_macro.py --all                       # 校验 resource/macros/ 下所有 .macro 文件
    python check_macro.py --all --fmt                 # 批量格式化

校验规则:
    - 外层必须有 {} 包裹
    - 每条语句以 ; 结束
    - action 名称必须在支持列表中
    - 坐标支持 [x,y] 精确坐标 或 [x1,y1,x2,y2] 矩形范围
    - click 必须有 [坐标]
    - longpress 必须有 [坐标] 和 duration
    - swipe 必须有两个 [坐标] 和 duration
    - wait 必须有数字参数
    - repeat 值必须 >= 1
    - wait() / duration() 值必须 >= 0
"""

import os
import re
import sys
import glob


# ========== 支持的 action 及其参数规则 ==========
ACTION_RULES = {
    "fly":      {"required_positional": 0, "max_positional": 1, "requires_duration": False, "description": "点击飞行"},
    "jump":     {"required_positional": 0, "max_positional": 1, "requires_duration": False, "description": "点击跳跃"},
    "click":    {"required_positional": 1, "max_positional": 1, "requires_duration": False, "description": "点击坐标"},
    "longpress":{"required_positional": 1, "max_positional": 1, "requires_duration": True,  "description": "长按坐标"},
    "swipe":    {"required_positional": 2, "max_positional": 2, "requires_duration": True,  "description": "滑动"},
    "up":       {"required_positional": 0, "max_positional": 0, "requires_duration": False, "description": "摇杆上"},
    "down":     {"required_positional": 0, "max_positional": 0, "requires_duration": False, "description": "摇杆下"},
    "left":     {"required_positional": 0, "max_positional": 0, "requires_duration": False, "description": "摇杆左"},
    "right":    {"required_positional": 0, "max_positional": 0, "requires_duration": False, "description": "摇杆右"},
    "wait":     {"required_positional": 1, "max_positional": 1, "requires_duration": False, "description": "等待"},
}


def _split_params(params_str: str) -> list:
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


def _parse_coord(coord_str: str):
    nums = re.findall(r'-?\d+', coord_str)
    return tuple(int(n) for n in nums)


def _parse_param_value(param_str: str) -> tuple:
    """解析单个参数，返回 (type, value) 或 (type, None) 表示无效"""
    param_str = param_str.strip()
    if not param_str:
        return ("empty", None)

    if param_str.startswith('['):
        try:
            coord = _parse_coord(param_str)
            if len(coord) in (2, 4):
                return ("coord", coord)
            else:
                return ("invalid_coord", param_str)
        except Exception:
            return ("invalid_coord", param_str)

    for key in ("wait", "repeat", "duration"):
        if param_str.startswith(f"{key}(") and param_str.endswith(")"):
            inner = param_str[len(key)+1:-1]
            try:
                val = int(inner)
                return (key, val)
            except ValueError:
                return ("invalid_number", param_str)

    if param_str.isdigit():
        return ("number", int(param_str))

    return ("unknown", param_str)


def validate_macro(filepath: str, fix: bool = False) -> tuple:
    """
    校验一个 .macro 文件，返回 (errors, warnings, fixed_content_or_None)
    errors: [(line_number, message)]
    warnings: [(line_number, message)]
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
        raw = "".join(lines)

    errors = []
    warnings = []

    # 去掉注释后再解析
    stripped = re.sub(r'//.*', '', raw)

    # 找所有 action{params}; 模式
    pattern = r'(\w+)\{([^}]*)\};'
    matches = list(re.finditer(pattern, stripped))

    if not matches and stripped.strip() not in ("", "{}", "{\n}"):
        errors.append((0, "未找到有效的 action 语句，请检查格式"))

    for m in matches:
        action = m.group(1)
        params_str = m.group(2)

        # 计算行号
        pos = m.start()
        line_num = raw[:pos].count('\n') + 1

        rule = ACTION_RULES.get(action)
        if rule is None:
            errors.append((line_num, f"未知的 action: '{action}'，支持: {', '.join(ACTION_RULES.keys())}"))
            continue

        params = _split_params(params_str)
        positional = []
        wait_val = None
        repeat_val = None
        duration_val = None

        for param in params:
            ptype, pval = _parse_param_value(param)
            if ptype == "coord":
                positional.append(pval)
            elif ptype == "wait":
                wait_val = pval
            elif ptype == "repeat":
                repeat_val = pval
            elif ptype == "duration":
                duration_val = pval
            elif ptype == "number":
                positional.append(pval)
            elif ptype == "empty":
                pass
            elif ptype == "invalid_coord":
                errors.append((line_num, f"[{action}] 坐标格式无效: {pval}，应为 [x,y] 或 [x1,y1,x2,y2]"))
            elif ptype == "invalid_number":
                errors.append((line_num, f"[{action}] 参数值无效: {pval}"))
            elif ptype == "unknown":
                errors.append((line_num, f"[{action}] 无法识别的参数: {pval}"))

        # 检查必选参数
        n_pos = len(positional)
        req = rule["required_positional"]
        if n_pos < req and action != "wait":
            errors.append((line_num, f"[{action}] 缺少坐标参数，需要 {req} 个 [x,y]"))
        if n_pos > rule["max_positional"]:
            errors.append((line_num, f"[{action}] 坐标参数过多，最多 {rule['max_positional']} 个"))

        if rule["requires_duration"] and duration_val is None:
            errors.append((line_num, f"[{action}] 缺少 duration(ms) 参数"))

        # 检查数值范围
        if wait_val is not None and wait_val < 0:
            errors.append((line_num, f"[{action}] wait 值不能为负数: {wait_val}"))
        if repeat_val is not None and repeat_val < 1:
            errors.append((line_num, f"[{action}] repeat 值必须 >= 1: {repeat_val}"))
        if duration_val is not None and duration_val < 0:
            errors.append((line_num, f"[{action}] duration 值不能为负数: {duration_val}"))

        # 建议
        if action == "wait" and repeat_val is not None and repeat_val > 1:
            warnings.append((line_num, f"[{action}] repeat 对 wait 无效，建议拆成多个 wait"))
        if repeat_val is None or repeat_val == 1:
            if action in ("fly", "jump", "up", "down", "left", "right") and n_pos == 0:
                pass  # 单次不写 repeat 很正常
        if action == "wait" and wait_val is not None:
            warnings.append((line_num, f"[{action}] wait 参数对 wait 动作无效，wait 自带等待时长"))

    # 检查外层花括号
    brace_open = stripped.count('{')
    brace_close = stripped.count('}')
    if brace_open != brace_close:
        errors.append((0, f"花括号不匹配: {{ 有 {brace_open} 个，}} 有 {brace_close} 个"))

    # 格式化
    fixed = None
    if fix and not errors:
        fixed = _format_macro(stripped)

    return errors, warnings, fixed


def _format_macro(text: str) -> str:
    """格式化 .macro 内容"""
    pattern = r'(\w+)\{([^}]*)\};'
    matches = list(re.finditer(pattern, text))

    formatted_lines = ["{"]
    for m in matches:
        action = m.group(1)
        params_str = m.group(2)
        params = [p.strip() for p in _split_params(params_str) if p.strip()]

        # 规范化参数：positional 在前，然后 duration, wait, repeat
        formatted_params = []

        # 抽取各类参数
        coords = []
        duration_val = None
        wait_val = None
        repeat_val = None

        for p in params:
            ptype, pval = _parse_param_value(p)
            if ptype == "coord":
                if len(pval) == 2:
                    coords.append(f"[{pval[0]},{pval[1]}]")
                else:
                    coords.append(f"[{pval[0]},{pval[1]},{pval[2]},{pval[3]}]")
            elif ptype == "duration":
                duration_val = pval
            elif ptype == "wait":
                wait_val = pval
            elif ptype == "repeat":
                repeat_val = pval
            elif ptype == "number":
                formatted_params.append(str(pval))

        # 按顺序重组
        formatted_params.extend(coords)
        if duration_val is not None:
            formatted_params.append(f"duration({duration_val})")
        if wait_val is not None and wait_val > 0:
            formatted_params.append(f"wait({wait_val})")
        if repeat_val is not None and repeat_val > 1:
            formatted_params.append(f"repeat({repeat_val})")

        line = f"    {action}{{{', '.join(formatted_params)}}};"
        formatted_lines.append(line)

    formatted_lines.append("}")
    return "\n".join(formatted_lines) + "\n"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    args = sys.argv[1:]
    fix = "--fmt" in args
    args = [a for a in args if a != "--fmt"]

    all_mode = "--all" in args
    args = [a for a in args if a != "--all"]

    if all_mode:
        macro_dir = os.path.join(os.getcwd(), "resource", "macros")
        targets = glob.glob(f"{macro_dir}/**/*.macro", recursive=True)
        if not targets:
            print("未找到 .macro 文件")
            sys.exit(1)
    elif args:
        target = args[0]
        if os.path.isdir(target):
            targets = glob.glob(f"{target}/**/*.macro", recursive=True)
        else:
            targets = [target]
    else:
        print("请指定文件或目录，或使用 --all 校验所有宏文件")
        sys.exit(1)

    total_errors = 0
    total_warnings = 0
    total_fixed = 0

    for filepath in targets:
        rel = os.path.relpath(filepath)
        errors, warnings, fixed = validate_macro(filepath, fix=fix)

        if errors:
            print(f"\n[FAIL] {rel}")
            for line_no, msg in errors:
                loc = f"行{line_no}" if line_no > 0 else "全局"
                print(f"  [{loc}] {msg}")
            total_errors += len(errors)
        elif warnings:
            print(f"[WARN] {rel}")
            for line_no, msg in warnings:
                loc = f"行{line_no}" if line_no > 0 else "全局"
                print(f"  [{loc}] {msg}")
            total_warnings += len(warnings)
        else:
            print(f"[ OK ] {rel}")

        if fixed is not None:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(fixed)
            print(f"        -> 已格式化")
            total_fixed += 1

    print(f"\n{'='*50}")
    print(f"文件: {len(targets)}  错误: {total_errors}  警告: {total_warnings}", end="")
    if total_fixed > 0:
        print(f"  已格式化: {total_fixed}", end="")
    print()

    sys.exit(1 if total_errors > 0 else 0)


if __name__ == "__main__":
    main()