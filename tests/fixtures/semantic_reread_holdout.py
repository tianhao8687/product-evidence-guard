"""12 new handwritten cases, frozen before production-path model A/B testing.

New synthetic holdout, NOT real customer data or a population accuracy estimate.
"""
from .semantic_rescue_pilot import case, fact, conflict


CASES = [
    case("h01", "新限定语", "紧固工具", "产品规格\n额定扭矩：not above 0.7N·m",
         [fact("torque", "≤0.7", "N·m", "rating:rated")]),
    case("h02", "新限定语", "电池", "产品规格\n电池容量：no fewer than 3100mAh",
         [fact("capacity_charge", "≥3100", "mAh")]),
    case("h03", "新限定语", "光电设备", "产品规格\n输出功率：circa 3W",
         [fact("power", "≈3", "W", "output")]),
    case("h04", "新字段", "嵌入式芯片", "Microcontroller peripheral specifications\n4 uart",
         [fact("custom:uart", "4")]),
    case("h05", "新字段", "存储芯片", "Memory specifications\n32 MB Fram",
         [fact("custom:fram", "32MB")]),
    case("h06", "新工况", "维护电源", "产品规格\n输入电压：7V（维护模式）\n输入电压：15V（连续模式）",
         [fact("voltage", 7, "V", "input|context:维护模式"), fact("voltage", 15, "V", "input|context:连续模式")]),
    case("h07", "新工况", "称重设备", "产品规格\n净重：125g before calibration", [], review=True),
    case("h08", "归属边界", "包装", [("products.txt", "SKU: BOX-A / BOX-B\n净重：285g")], [], review=True),
    case("h09", "冲突边界", "照明设备", ["产品规格\n输出功率：4W", "产品规格\n输出功率：5W"],
         [conflict("power", [4, 5], "W", "output")]),
    case("h10", "否定边界", "电源维修", "维修记录\n输入电压：not 16V", [], review=True),
    case("h11", "清楚参数", "音响", "产品规格\n输出功率：18W", [fact("power", 18, "W", "output")]),
    case("h12", "清楚参数", "物流", ["产品规格\n净重：0.73kg", "产品规格\n净重：730g"],
         [fact("net_weight", 730, "g", "net")]),
]
