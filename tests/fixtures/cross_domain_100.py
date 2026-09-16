"""Authored inputs and independent literal gold; NO production-code imports.

20 public-source adaptations + 80 fictional, domain-specific stress packages.
This is the readable curation source. Freeze its emitted JSON before execution.
Numeric answers below are manually specified, never obtained from the extractor.
Unit-case v2 amends old lowercased expected symbols against the literal inputs;
GHz is now supported. These exposed regressions are not new blind evidence.
"""
import json
from pathlib import Path

CASES = []
DOMAINS = ["industrial_power", "networking", "motion", "battery", "cooling",
           "solar", "environmental_sensor", "power_tool", "adhesive", "metrology"]
SOURCES = {
    "industrial_power": "https://www.meanwell.com/Upload/PDF/LRS-35/LRS-35-SPEC.PDF",
    "networking": "https://static.tp-link.com/res/down/doc/TL-SG105_V2_Datasheet.pdf",
    "motion": "https://www.pololu.com/category/271/12v-37d-metal-gearmotors",
    "battery": "https://www.victronenergy.com/media/pg/Lithium_Battery_Smart/en/technical-data.html",
    "cooling": "https://cdn.noctua.at/media/noctua_nf_a8_pwm_infosheet_en_web.pdf",
    "solar": "https://static.csisolar.com/wp-content/uploads/2020/06/27131418/CS-Datasheet-HiKu6_CS6R-MS_v2.0_EN.pdf",
    "environmental_sensor": "https://sensirion.com/products/catalog/SCD41",
    "power_tool": "https://www.makita.co.nz/products/model/DDF490Z",
    "adhesive": "https://www.3m.com/3M/en_US/p/d/b40072022/",
    "metrology": "https://www.fluke.com/en-us/product/electrical-testing/digital-multimeters/fluke-117",
}


def owner(sku=None, model=None, variant=None):
    # Same identity contract as the product: explicit SKU is the primary key.
    return {"sku": sku} if sku else {"model": model, "variant": variant}


def f(who, field, value, unit=None, scope=None, status="verified"):
    return [owner(who) if isinstance(who, str) else who, field, scope, status, [[value, unit]]]


def identity(sku):
    return f(sku, "sku", sku.casefold())


def conflict(sku, field, pairs, scope=None):
    return [owner(sku), field, scope, "conflict", pairs]


def package(domain, number, title, risk, files, facts, *, public_model=None, source=None, note=""):
    CASES.append({
        "id": f"{DOMAINS.index(domain)+1:02d}-{number:02d}", "domain": domain,
        "title": title, "family": risk, "identity_mode": "product_key",
        "split": "holdout" if domain in {"adhesive", "metrology"} else "development",
        "source_kind": "public_adapted" if public_model else "synthetic_stress",
        "source_url": source or SOURCES[domain], "source_model": public_model,
        "provenance": ("Selected factual specifications; translated/reformatted; SKU replaced. "
                       "Not an original-document end-to-end test. " if public_model else
                       "Fictional product and values; domain reference is inspiration, NOT evidence of these claims. ") + note,
        "files": files, "facts": facts,
    })


def textpack(domain, number, title, risk, sku, lines, facts, **kwargs):
    package(domain, number, title, risk, {"spec.txt": f"SKU: {sku}\n{lines}\n"},
            [identity(sku), *facts], **kwargs)


# Industrial power: input/output, efficiency, per-channel limits, bus alternatives.
D = "industrial_power"
textpack(D,1,"LRS-35-15 输出参数选摘","public_power_matrix","PS15",
         "输出电压: 15V\n额定电流: 2.4A\n额定功率: 36W",
         [f("PS15","voltage",15,"V","output"), f("PS15","current",2.4,"A","rating:rated"),
          f("PS15","power",36,"W","rating:rated")], public_model="LRS-35-15", note="PDF page 2.")
package(D,2,"LRS-35-36 横向参数表","public_power_matrix",
        {"spec.csv":"SKU,输出电压(V),额定电流(A),额定功率(W)\nPS36,36,1,36\n"},
        [identity("PS36"),f("PS36","voltage",36,"V","output"),f("PS36","current",1,"A","rating:rated"),
         f("PS36","power",36,"W","rating:rated")],public_model="LRS-35-36",note="PDF page 2, table units moved to header.")
package(D,3,"双路适配器 JSON 商品隔离","json_product_array",
        {"products.json":json.dumps([{"SKU":"ADAPTER-A","输入电压":"100~240V","输出电压":"12V"},
                                    {"SKU":"ADAPTER-B","输入电压":"220V","输出电压":"24V"}],ensure_ascii=False)},
        [identity("ADAPTER-A"),identity("ADAPTER-B"),f("ADAPTER-A","voltage",[100,240],"V","input"),
         f("ADAPTER-A","voltage",12,"V","output"),f("ADAPTER-B","voltage",220,"V","input"),f("ADAPTER-B","voltage",24,"V","output")])
textpack(D,4,"转换模块相同数字不同方向","electrical_direction","CONVERTER",
         "输入电压: 24V\n输出电压: 24V\n转换效率: 93%",
         [f("CONVERTER","voltage",24,"V","input"),f("CONVERTER","voltage",24,"V","output"),f("CONVERTER","custom:转换效率","93%")])
package(D,5,"充电器少数来源反例","minority_conflict",
        {"manual.txt":"SKU: CHARGER\n输出电压: 19V\n额定电流: 3A",
         "label.txt":"SKU: CHARGER\n输出电压: 20V","copy.txt":"SKU: CHARGER\n输出电压: 19V"},
        [identity("CHARGER"),conflict("CHARGER","voltage",[[19,"V"],[20,"V"]],"output"),f("CHARGER","current",3,"A","rating:rated")])
textpack(D,6,"多档输出完整保留而非只取第一值","alternative_rails","MULTIRAIL",
         "输出电压: 3.3V / 5V / 12V\n额定功率: 60W\n绝缘类别: Class II",
         [f("MULTIRAIL","voltage",[3.3,5,12],"V","output"),
          f("MULTIRAIL","power",60,"W","rating:rated"),f("MULTIRAIL","custom:绝缘类别","class ii")])
textpack(D,7,"限流电源上下限不同字段","rated_and_max","BENCHSUPPLY",
         "额定电流: 2A\n最大电流: 3A\n输入电压: 230V 50Hz",
         [f("BENCHSUPPLY","current",2,"A","rating:rated"),f("BENCHSUPPLY","current",3,"A","rating:max"),f("BENCHSUPPLY","voltage",230,"V","input")])
package(D,8,"隔离电源多文件换算一致","power_conversion",
        {"spec.txt":"SKU: ISOLATOR\n额定功率: 0.024kW\n输出电压: 12000mV",
         "plate.txt":"SKU: ISOLATOR\n额定功率: 24W\n输出电压: 12V"},
        [identity("ISOLATOR"),f("ISOLATOR","power",24,"W","rating:rated"),f("ISOLATOR","voltage",12,"V","output")])
textpack(D,9,"驱动电源复合单位不得降成普通功率","per_channel","LEDDRIVER",
         "额定功率: 30W/channel\n通道数: 4\n输入电压: 48V",
         [f("LEDDRIVER","power","30W/channel",None,"rating:rated","pending_confirmation"),
          f("LEDDRIVER","custom:通道数","4"),f("LEDDRIVER","voltage",48,"V","input")])
package(D,10,"继电模块明确修订版本","body_version",
        {"a.txt":"资料版本: 1.0\nSKU: RELAY\n额定电流: 5A\n接口: 螺钉端子",
         "b.txt":"资料版本: 2.0\nSKU: RELAY\n额定电流: 8A\n接口: 螺钉端子"},
        [identity("RELAY"),[owner("RELAY"),"current","rating:rated","pending_confirmation",[[5,"A"],[8,"A"]]],f("RELAY","interface","螺钉端子")])

# Networking: opaque lists, quoting, unknown headers, local product context.
D = "networking"
textpack(D,1,"TL-SG105 环境与功耗","public_switch","SW105",
         "最大功率: 3.2W\n产品尺寸: 99.8x98x25mm\n工作温度: 0~40°C",
         [f("SW105","power",3.2,"W","rating:max"),f("SW105","dimensions",[99.8,98,25],"mm","product"),
          f("SW105","temperature",[0,40],"°C","operating")],public_model="TL-SG105 V2",note="PDF page 3; metric dimensions only.")
package(D,2,"TL-SG108 JSON 参数单","public_switch",{"spec.json":json.dumps({"SKU":"SW108","最大功率":"3.97W","工作温度":"0~40°C","交换容量":"16Gbps"},ensure_ascii=False)},
        [identity("SW108"),f("SW108","power",3.97,"W","rating:max"),f("SW108","temperature",[0,40],"°C","operating"),f("SW108","custom:交换容量","16Gbps")],public_model="TL-SG108 V2",note="PDF page 3.")
package(D,3,"接入点嵌套 JSON 继承","json_nested_specs",{"spec.json":json.dumps({"SKU":"ACCESSPOINT","specs":{"输入电压":"12V","协议":"802.11ax","工作频率":"2.4GHz"}},ensure_ascii=False)},
        [identity("ACCESSPOINT"),f("ACCESSPOINT","voltage",12,"V","input"),f("ACCESSPOINT","protocol","802.11ax"),f("ACCESSPOINT","frequency",2400000000,"Hz","operating")])
package(D,4,"路由器逗号列表与转义引号","csv_quoting",{"spec.csv":'SKU,协议,接口,输入电压\nROUTER,"TCP, UDP, ICMP","USB-C, RJ45",12V\n'},
        [identity("ROUTER"),f("ROUTER","protocol","tcp, udp, icmp"),f("ROUTER","interface","usb-c, rj45"),f("ROUTER","voltage",12,"V","input")])
package(D,5,"光模块新字段不依赖白名单","unknown_digit_header",{"optics.csv":"SKU,光波长(nm),传输距离(km),连接器\nOPTICS,1310,10,LC\n"},
        [identity("OPTICS"),f("OPTICS","custom:光波长","1310 nm"),f("OPTICS","custom:传输距离","10 km"),f("OPTICS","custom:连接器","lc")])
textpack(D,6,"网关两种环境口径","operating_storage","GATEWAY",
         "工作温度: -20~60°C\n储存温度: -40~85°C\n防护等级: IP67",
         [f("GATEWAY","temperature",[-20,60],"°C","operating"),f("GATEWAY","temperature",[-40,85],"°C","storage"),f("GATEWAY","ip_rating","ip67")])
package(D,7,"PoE 设备独立端口功率","qualified_open_fields",{"switch.txt":"SKU: POESW\n整机功率预算: 120W\n单端口供电功率: 30W\n接口: RJ45"},
        [identity("POESW"),f("POESW","custom:整机功率预算","120W"),f("POESW","custom:单端口供电功率","30W"),f("POESW","interface","rj45")])
package(D,8,"两种网卡型号仅型号归属","model_only_products",{"cards.csv":"型号,接口,协议\nCARD-USB,USB-C,USB3.2\nCARD-PCIE,PCIe,PCIe4.0\n"},
        [f(owner(model="CARD-USB"),"model","card-usb"),f(owner(model="CARD-USB"),"interface","usb-c"),f(owner(model="CARD-USB"),"protocol","usb3.2"),
         f(owner(model="CARD-PCIE"),"model","card-pcie"),f(owner(model="CARD-PCIE"),"interface","pcie"),f(owner(model="CARD-PCIE"),"protocol","pcie4.0")])
textpack(D,9,"交换机 Markdown 标题不是参数","markdown_metadata","RACKSW",
         "# 产品说明\n\n- 接口: SFP+\n- 额定功率: 80W\n注意: 使用接地插座",
         [f("RACKSW","interface","sfp+"),f("RACKSW","power",80,"W","rating:rated")])
package(D,10,"中继器复制资料不得制造事实","renamed_copies",{"a.txt":"SKU: REPEATER\n输入电压: 5V\n接口: USB-C","b.txt":"SKU: REPEATER\n输入电压: 5V\n接口: USB-C"},
        [identity("REPEATER"),f("REPEATER","voltage",5,"V","input"),f("REPEATER","interface","usb-c")])

# Motion: test conditions in the label, torque unit conversion, SKU section boundaries.
D = "motion"
textpack(D,1,"37D 19:1 减速电机","public_gearmotor","GEAR19",
         "额定电压: 12V\n空载转速: 530RPM\n减速比: 19:1\n空载电流: 0.2A",
         [f("GEAR19","voltage",12,"V","rating:rated"),f("GEAR19","custom:空载转速","530RPM"),f("GEAR19","custom:减速比","19:1"),f("GEAR19","custom:空载电流","0.2A")],public_model="Pololu 4741",note="12V 37D comparison table.")
textpack(D,2,"37D 50:1 堵转参数","public_gearmotor","GEAR50",
         "额定电压: 12V\n空载转速: 200RPM\n堵转电流: 5.5A",
         [f("GEAR50","voltage",12,"V","rating:rated"),f("GEAR50","custom:空载转速","200RPM"),f("GEAR50","custom:堵转电流","5.5A")],public_model="Pololu 4743",note="Stall current is theoretical extrapolation, not continuous current.")
textpack(D,3,"伺服的额定与峰值扭矩","torque_scopes","SERVO",
         "额定扭矩: 2N·m\n最大扭矩: 6N·m\n控制方式: 脉冲",
         [f("SERVO","torque",2,"N·m","rating:rated"),f("SERVO","torque",6,"N·m","rating:max"),f("SERVO","custom:控制方式","脉冲")])
package(D,4,"减速箱扭矩单位换算","torque_conversion",{"a.txt":"SKU: GEARBOX\n额定扭矩: 10kgf·cm\n材质: 钢","b.txt":"SKU: GEARBOX\n额定扭矩: 0.980665N·m"},
        [identity("GEARBOX"),f("GEARBOX","torque",0.980665,"N·m","rating:rated"),f("GEARBOX","material","钢")])
package(D,5,"气缸两个商品段落","sku_section",{"spec.txt":"SKU: CYL-A\n工作压力: 0.6MPa\n行程: 50mm\nSKU: CYL-B\n工作压力: 0.8MPa\n行程: 100mm"},
        [identity("CYL-A"),identity("CYL-B"),f("CYL-A","pressure",600000,"Pa","operating"),f("CYL-A","custom:行程","50mm"),f("CYL-B","pressure",800000,"Pa","operating"),f("CYL-B","custom:行程","100mm")])
package(D,6,"泵工作与最大流量","flow_scope",{"spec.csv":"SKU,工作流量,最大流量,最大压力\nMICROPUMP,500mL/min,1L/min,2bar\n"},
        [identity("MICROPUMP"),f("MICROPUMP","flow_rate",.5,"L/min","operating"),f("MICROPUMP","flow_rate",1,"L/min","rating:max"),f("MICROPUMP","pressure",200000,"Pa","rating:max")])
textpack(D,7,"步进电机相电流不是电源电流","phase_conditions","STEPPER",
         "相电流: 1.5A\n输入电流: 0.7A\n步距角: 1.8°",
         [f("STEPPER","custom:相电流","1.5A"),f("STEPPER","current",.7,"A","input"),f("STEPPER","custom:步距角","1.8°")])
textpack(D,8,"编码器增量参数与小数点","encoder_custom","ENCODER",
         "分辨率: 1024PPR\n额定电压: 5V\n接口: RS422",
         [f("ENCODER","custom:分辨率","1024PPR"),f("ENCODER","voltage",5,"V","rating:rated"),f("ENCODER","interface","rs422")])
package(D,9,"轴承规格带小数与范围","bearing_geometry",{"spec.csv":"SKU,内径(mm),外径(mm),宽度(mm),材质\nBEARING,8,22,7,轴承钢\n"},
        [identity("BEARING"),f("BEARING","custom:内径","8 mm"),f("BEARING","custom:外径","22 mm"),f("BEARING","width",7,"mm"),f("BEARING","material","轴承钢")])
textpack(D,10,"直线导轨复合精度单位","precision_per_length","RAIL",
         "长度: 400mm\n直线度: 0.02mm/m\n材质: 不锈钢",
         [f("RAIL","length",400,"mm"),f("RAIL","custom:直线度","0.02mm/m"),f("RAIL","material","不锈钢")])

# Batteries: temperature-conditioned capacity must not become nominal unqualified capacity.
D = "battery"
textpack(D,1,"LFP-Smart 50Ah 名义参数","public_lfp","LFP50",
         "标称电压: 12.8V\n25°C容量: 50Ah\n重量: 7kg\n防护等级: IP22",
         [f("LFP50","voltage",12.8,"V","rating:nominal"),f("LFP50","custom:25°c容量","50Ah"),f("LFP50","weight",7000,"g","unspecified"),f("LFP50","ip_rating","ip22")],public_model="LFP-Smart 12.8/50",note="Capacity at 25 C and discharge <=1C; label retains temperature.")
textpack(D,2,"LFP-Smart 不同温度容量","public_lfp","LFP100",
         "标称电压: 12.8V\n电池容量(25°C): 100Ah\n电池容量(0°C): 80Ah\n重量: 14kg",
         [f("LFP100","voltage",12.8,"V","rating:nominal"),f("LFP100","custom:电池容量(25°c)","100Ah"),f("LFP100","custom:电池容量(0°c)","80Ah"),f("LFP100","weight",14000,"g","unspecified")],public_model="LFP-Smart 12.8/100",note="Two temperature conditions, discharge <=1C; no silent condition stripping.")
package(D,3,"充电宝 Ah 与 mAh 一致","charge_conversion",{"a.txt":"SKU: POWERBANK\n电池容量: 10Ah\n输出电压: 5V","b.txt":"SKU: POWERBANK\n电池容量: 10000mAh"},
        [identity("POWERBANK"),f("POWERBANK","capacity_charge",10000,"mAh"),f("POWERBANK","voltage",5,"V","output")])
textpack(D,4,"储能电芯容量与能量不混用","charge_energy","CELLENERGY",
         "电池容量: 50Ah\n额定能量: 160Wh\n标称电压: 3.2V",
         [f("CELLENERGY","capacity_charge",50000,"mAh"),f("CELLENERGY","custom:额定能量","160Wh"),f("CELLENERGY","voltage",3.2,"V","rating:nominal")])
package(D,5,"电池系列 JSON 嵌套商品","json_child_products",{"batteries.json":json.dumps({"products":[{"SKU":"PACK-A","specs":{"电池容量":"2Ah","标称电压":"18V"}},{"SKU":"PACK-B","specs":{"电池容量":"4Ah","标称电压":"18V"}}]},ensure_ascii=False)},
        [identity("PACK-A"),identity("PACK-B"),f("PACK-A","capacity_charge",2000,"mAh"),f("PACK-A","voltage",18,"V","rating:nominal"),f("PACK-B","capacity_charge",4000,"mAh"),f("PACK-B","voltage",18,"V","rating:nominal")])
textpack(D,6,"低温电池不同充放电温度","charge_discharge_temp","COLDPACK",
         "充电温度: 0~45°C\n放电温度: -20~60°C\n储存温度: -10~30°C",
         [f("COLDPACK","custom:充电温度","0~45°C"),f("COLDPACK","custom:放电温度","-20~60°C"),f("COLDPACK","temperature",[-10,30],"°C","storage")])
textpack(D,7,"电池包续航与充电时间","duration_conversion","UPS",
         "续航时间: 1.5h\n充电时间: 90min\n接口: DC5521",
         [f("UPS","runtime",5400,"s"),f("UPS","charging_time",5400,"s"),f("UPS","interface","dc5521")])
textpack(D,8,"电芯最低容量不能变成精确值","capacity_bound","MINCELL",
         "电池容量: 不低于2500mAh\n标称电压: 3.7V\n化学体系: NMC",
         [f("MINCELL","capacity_charge","≥2500","mAh"),f("MINCELL","voltage",3.7,"V","rating:nominal"),f("MINCELL","custom:化学体系","nmc")])
textpack(D,9,"储能组件缺单位需要局部确认","missing_unit","BARECAP",
         "电池容量: 3000\n标称电压: 48V\n化学体系: LFP",
         [f("BARECAP","capacity_charge","3000",None,None,"pending_confirmation"),f("BARECAP","voltage",48,"V","rating:nominal"),f("BARECAP","custom:化学体系","lfp")])
package(D,10,"两来源电芯容量冲突","capacity_conflict",{"lab.txt":"SKU: CELLCONFLICT\n电池容量: 2600mAh\n重量: 46g","spec.txt":"SKU: CELLCONFLICT\n电池容量: 2800mAh\n重量: 46g"},
        [identity("CELLCONFLICT"),conflict("CELLCONFLICT","capacity_charge",[[2600,"mAh"],[2800,"mAh"]]),f("CELLCONFLICT","weight",46,"g","unspecified")])

# Cooling: single/box quantities, unknown field units and separate acoustic modes.
D = "cooling"
textpack(D,1,"NF-A8 参数与包装区分","public_fan","FAN80",
         "产品尺寸: 80x80x25mm\n输入功率: 0.96W\n毛重: 245g\n保修期: 6年",
         [f("FAN80","dimensions",[80,80,25],"mm","product"),f("FAN80","power",.96,"W","input"),f("FAN80","gross_weight",245,"g","gross"),f("FAN80","custom:保修期","6年")],public_model="NF-A8 PWM",note="Single packaged product weight, not 11.30 kg master carton.")
textpack(D,2,"NF-A12x25 5V 与包装单位","public_fan","FAN120",
         "输入电压: 5V\n输入功率: 1.75W\n产品尺寸: 120x120x25mm\n毛重: 364g",
         [f("FAN120","voltage",5,"V","input"),f("FAN120","power",1.75,"W","input"),f("FAN120","dimensions",[120,120,25],"mm","product"),f("FAN120","gross_weight",364,"g","gross")],public_model="NF-A12x25 5V PWM",source="https://cdn.noctua.at/media/noctua_nf_a12x25_5v_pwm_infosheet_en_web.pdf")
textpack(D,3,"风机静音与性能档噪声","noise_modes","DUCTFAN",
         "静音档噪声: 18dB(A)\n性能档噪声: 28dB(A)\n输入电压: 24V",
         [f("DUCTFAN","custom:静音档噪声","18dB(a)"),f("DUCTFAN","custom:性能档噪声","28dB(a)"),f("DUCTFAN","voltage",24,"V","input")])
package(D,4,"散热器 mm 与 cm 尺寸","dim_conversion",{"a.txt":"SKU: HEATSINK\n产品尺寸: 10x5x2cm\n材质: 铝","b.txt":"SKU: HEATSINK\n产品尺寸: 100x50x20mm"},
        [identity("HEATSINK"),f("HEATSINK","dimensions",[100,50,20],"mm","product"),f("HEATSINK","material","铝")])
textpack(D,5,"包装数量与单品毛重","pack_count","FANBOX",
         "包装数量: 24pcs\n毛重: 0.25kg\n整箱毛重: 6.8kg",
         [f("FANBOX","quantity",24,"count"),f("FANBOX","gross_weight",250,"g","gross"),f("FANBOX","custom:整箱毛重","6.8kg")])
package(D,6,"风扇表中方括号单位","square_bracket_units",{"fans.csv":"SKU,输入电压[V],输入功率[W],噪声[dB(A)]\nBRACKETFAN,12,2.4,25\n"},
        [identity("BRACKETFAN"),f("BRACKETFAN","voltage",12,"V","input"),f("BRACKETFAN","power",2.4,"W","input"),f("BRACKETFAN","custom:噪声","25 dB(a)")])
textpack(D,7,"循环泵流量与扬程分开","pump_custom","COOLPUMP",
         "工作流量: 2L/min\n扬程: 3m\n输入电压: 12V",
         [f("COOLPUMP","flow_rate",2,"L/min","operating"),f("COOLPUMP","custom:扬程","3m"),f("COOLPUMP","voltage",12,"V","input")])
textpack(D,8,"风机寿命不是单次续航","lifetime_custom","LONGFAN",
         "MTTF: >150000h\n运行时间: 8h\n轴承类型: 双滚珠",
         [f("LONGFAN","custom:mttf",">150000h"),f("LONGFAN","runtime",28800,"s"),f("LONGFAN","custom:轴承类型","双滚珠")])
textpack(D,9,"冷板毫米公差不应丢失","length_tolerance","COLDPLATE",
         "长度: 100±0.2mm\n宽度: 80mm\n材质: 铜",
         [f("COLDPLATE","length","100±0.2","mm"),f("COLDPLATE","width",80,"mm"),f("COLDPLATE","material","铜")])
package(D,10,"冷热模块相同描述一致","custom_agreement",{"a.txt":"SKU: PELTIER\n冷热模式: 制冷/制热\n输入电压: 12V","b.txt":"SKU: PELTIER\n冷热模式: 制冷/制热\n输入电压: 12000mV"},
        [identity("PELTIER"),f("PELTIER","custom:冷热模式","制冷/制热"),f("PELTIER","voltage",12,"V","input")])

# Solar: conditional ratings, derivatives, nested telemetry, negative values.
D = "solar"
textpack(D,1,"CS6R-400MS STC 参数","public_pv_conditions","PV400",
         "STC最大功率: 400W\nSTC工作电压: 30.8V\nSTC工作电流: 12.99A\n工作温度: -40~85°C",
         [f("PV400","custom:stc最大功率","400W"),f("PV400","custom:stc工作电压","30.8V"),f("PV400","custom:stc工作电流","12.99A"),f("PV400","temperature",[-40,85],"°C","operating")],public_model="CS6R-400MS",note="Page 2 STC irradiance 1000 W/m2, cell 25 C, AM1.5. Conditions explicitly retained, not a generic maximum.")
textpack(D,2,"CS6R-405MS STC 对比 NMOT","public_pv_conditions","PV405",
         "STC最大功率: 405W\nNMOT最大功率: 304W\n重量: 21.3kg",
         [f("PV405","custom:stc最大功率","405W"),f("PV405","custom:nmot最大功率","304W"),f("PV405","weight",21300,"g","unspecified")],public_model="CS6R-405MS",note="Page 2 separate STC and NMOT tables, not a conflict.")
textpack(D,3,"双面组件温度系数","temperature_coefficient","BIFACIAL",
         "功率温度系数: -0.34%/°C\n双面率: 80%\n防护等级: IP68",
         [f("BIFACIAL","custom:功率温度系数","-0.34%/°C"),f("BIFACIAL","custom:双面率","80%"),f("BIFACIAL","ip_rating","ip68")])
package(D,4,"组件参数条件写在括号","parenthetical_conditions",{"pv.csv":"SKU,功率(STC),功率(NMOT),重量(kg)\nPVCOND,430W,325W,22.5\n"},
        [identity("PVCOND"),f("PVCOND","custom:功率(stc)","430W"),f("PVCOND","custom:功率(nmot)","325W"),f("PVCOND","weight",22500,"g","unspecified")])
textpack(D,5,"控制器极限电压与工作电压","controller_scope","MPPT",
         "最大电压: 100V\n输入电压: 20~80V\n额定电流: 30A",
         [f("MPPT","voltage",100,"V","rating:max"),f("MPPT","voltage",[20,80],"V","input"),f("MPPT","current",30,"A","rating:rated")])
textpack(D,6,"支架装配尺寸与零件长度","mount_geometry","PVMOUNT",
         "产品尺寸: 1200x800x400mm\n长度: 1.2m\n材质: 铝合金",
         [f("PVMOUNT","dimensions",[1200,800,400],"mm","product"),f("PVMOUNT","length",1200,"mm"),f("PVMOUNT","material","铝合金")])
package(D,7,"逆变器带备注的分号表","semicolon_nested_quotes",{"inverter.csv":'SKU;输入电压;输出电压;接口\nINVERTER;48V;230V;"RS485; CAN"\n'},
        [identity("INVERTER"),f("INVERTER","voltage",48,"V","input"),f("INVERTER","voltage",230,"V","output"),f("INVERTER","interface","rs485; can")])
textpack(D,8,"光伏线缆面积单位不当长度","area_not_length","PVCABLE",
         "导体截面积: 4mm²\n长度: 10m\n颜色: 黑色",
         [f("PVCABLE","custom:导体截面积","4mm²"),f("PVCABLE","length",10000,"mm"),f("PVCABLE","color","黑色")])
package(D,9,"接线盒结构化重复键不得无声覆盖","json_duplicate_key",{"spec.json":'{"SKU":"JBOX","输入电压":"1000V","输入电压":"1500V","防护等级":"IP68"}'}, [],note="Invalid duplicate keys must produce a reported parse error, not choose the last rating.")
CASES[-1]["expected_error"] = "duplicate_json_key"
textpack(D,10,"微逆额定功率中的负值","negative_power","MICROINV",
         "额定功率: -600W\n接口: AC端子\n防护等级: IP67",
         [f("MICROINV","power",-600,"W","rating:rated","pending_confirmation"),f("MICROINV","interface","ac端子"),f("MICROINV","ip_rating","ip67")])

# Environment sensing: opaque chemicals/units, metadata exclusion, nulls and unit objects.
D = "environmental_sensor"
textpack(D,1,"SCD41 环境与供电","public_sensor","CO2SENSOR",
         "输入电压: 2.4~5.5V\n工作温度: -10~60°C\n产品尺寸: 10.1x10.1x6.5mm",
         [f("CO2SENSOR","voltage",[2.4,5.5],"V","input"),f("CO2SENSOR","temperature",[-10,60],"°C","operating"),f("CO2SENSOR","dimensions",[10.1,10.1,6.5],"mm","product")],public_model="SCD41")
textpack(D,2,"SHT31 小尺寸与宽温度","public_sensor","HUMIDSENSOR",
         "输入电压: 2.4~5.5V\n工作温度: -40~125°C\n产品尺寸: 2.5x2.5x0.9mm",
         [f("HUMIDSENSOR","voltage",[2.4,5.5],"V","input"),f("HUMIDSENSOR","temperature",[-40,125],"°C","operating"),f("HUMIDSENSOR","dimensions",[2.5,2.5,.9],"mm","product")],public_model="SHT31-DIS-B",source="https://sensirion.com/products/catalog/SHT31-DIS-B")
package(D,3,"气体传感器带数字的新表头","digit_custom_field",{"gas.csv":"SKU,CO2量程,PM2.5量程,接口\nAIRMON,400-5000ppm,0-1000ug/m3,I2C\n"},
        [identity("AIRMON"),f("AIRMON","custom:co2量程","400-5000ppm"),f("AIRMON","custom:pm2.5量程","0-1000ug/m3"),f("AIRMON","interface","i2c")])
textpack(D,4,"湿度与温度相同数字不合并","different_parameters","THSENSOR",
         "湿度量程: 0~100%RH\n工作温度: 0~100°C\n接口: I2C",
         [f("THSENSOR","custom:湿度量程","0~100%RH"),f("THSENSOR","temperature",[0,100],"°C","operating"),f("THSENSOR","interface","i2c")])
package(D,5,"采集模块 value-unit JSON","json_value_unit",{"spec.json":json.dumps({"SKU":"DAQ","输入电压":{"value":5,"unit":"V"},"重量":{"value":20,"unit":"g"},"接口":"USB"},ensure_ascii=False)},
        [identity("DAQ"),f("DAQ","voltage",5,"V","input"),f("DAQ","weight",20,"g","unspecified"),f("DAQ","interface","usb")])
textpack(D,6,"传感探头科学计数法","scientific_notation","PROBE",
         "额定电流: 2e-3A\n输入电压: 5V\n接口: 模拟",
         [f("PROBE","current",.002,"A","rating:rated"),f("PROBE","voltage",5,"V","input"),f("PROBE","interface","模拟")])
package(D,7,"数据记录器 JSON 空值不能制造 None 字符串","json_null",{"spec.json":json.dumps({"SKU":"LOGGER","重量":None,"输入电压":"3.3V","接口":"UART"},ensure_ascii=False)},
        [identity("LOGGER"),f("LOGGER","voltage",3.3,"V","input"),f("LOGGER","interface","uart")],note="Absent/null attribute is not a claimed fact; missing-source coverage tracked separately.")
textpack(D,8,"仪表元数据标签不可变事实","metadata_labels","AIRQUALITY",
         "测量原理: 激光散射\n校准周期: 12个月\n注意: 避免凝露\n文档版本: 1.0",
         [f("AIRQUALITY","custom:测量原理","激光散射"),f("AIRQUALITY","custom:校准周期","1年")])
textpack(D,9,"压力传感器保留精度表达","accuracy_expression","PRESSENSOR",
         "工作压力: 0~1MPa\n测量精度: ±0.5%FS\n输入电压: 24V",
         [f("PRESSENSOR","pressure",[0,1000000],"Pa","operating"),f("PRESSENSOR","custom:测量精度","±0.5%FS"),f("PRESSENSOR","voltage",24,"V","input")])
package(D,10,"监测器单位大小写不得误认微安","micro_current",{"micro.csv":"SKU,输入电压,额定电流,接口\nMICROSENSOR,3V,20uA,SPI\n"},
        [identity("MICROSENSOR"),f("MICROSENSOR","voltage",3,"V","input"),f("MICROSENSOR","current",.00002,"A","rating:rated"),f("MICROSENSOR","interface","spi")])

# Power tools: product subcomponents, unknown conditions, invalid numbers, trusted boundaries.
D = "power_tool"
textpack(D,1,"DDF490Z 裸机规格","public_drill","DRILL490",
         "裸机净重: 1.0kg\n最大扭矩: 65Nm\n钢材钻孔直径: 13mm",
         [f("DRILL490","custom:裸机净重","1.0kg"),f("DRILL490","torque",65,"N·m","rating:max"),f("DRILL490","custom:钢材钻孔直径","13mm")],public_model="DDF490Z",note="Skin weight, not battery-installed weight.")
textpack(D,2,"DDF484Z 材料钻孔能力","public_drill","DRILL484",
         "净重: 1.5kg\n钢材钻孔直径: 13mm\n木材钻孔直径: 38mm",
         [f("DRILL484","net_weight",1500,"g","net"),f("DRILL484","custom:钢材钻孔直径","13mm"),f("DRILL484","custom:木材钻孔直径","38mm")],public_model="DDF484Z",source="https://www.makita.co.nz/products/model/DDF484Z",note="Torque not used: headline 60Nm and table 54Nm use different wording.")
package(D,3,"冲击扳手工具与附件商品","json_child_identity",{"spec.json":json.dumps({"SKU":"WRENCH","净重":"1.2kg","附件":{"SKU":"SOCKET","净重":"200g","材质":"钢"}},ensure_ascii=False)},
        [identity("WRENCH"),identity("SOCKET"),f("WRENCH","net_weight",1200,"g","net"),f("SOCKET","net_weight",200,"g","net"),f("SOCKET","material","钢")])
textpack(D,4,"砂轮机空载转速和负载不同","rpm_modes","GRINDER",
         "空载转速: 10000rpm\n额定负载转速: 8000rpm\n砂轮直径: 125mm",
         [f("GRINDER","custom:空载转速","10000rpm"),f("GRINDER","custom:额定负载转速","8000rpm"),f("GRINDER","custom:砂轮直径","125mm")])
textpack(D,5,"热风枪档位温度不取第一个","temperature_options","HEATGUN",
         "工作温度: 300°C / 500°C\n额定功率: 1500W\n接口: 欧规插头",
         [f("HEATGUN","temperature","300°C / 500°C",None,"operating","pending_confirmation"),f("HEATGUN","power",1500,"W","rating:rated"),f("HEATGUN","interface","欧规插头")])
textpack(D,6,"测距工具否定后明确更正","explicit_correction","DISTANCE",
         "净重不是300g，正确为净重: 250g\n测量范围: 0.05~40m\n防护等级: IP54",
         [f("DISTANCE","net_weight",250,"g","net"),f("DISTANCE","custom:测量范围","0.05~40m"),f("DISTANCE","ip_rating","ip54")])
textpack(D,7,"电锯铭牌千分逗号与小数","thousands","SAW",
         "额定功率: 1,800W\n净重: 4.25kg\n锯片直径: 185mm",
         [f("SAW","power",1800,"W","rating:rated"),f("SAW","net_weight",4250,"g","net"),f("SAW","custom:锯片直径","185mm")])
package(D,8,"批头套装错误数量只阻断自身","nonintegral_count",{"spec.csv":"SKU,包装数量,材质,颜色\nBITSET,12.5pcs,S2钢,银色\n"},
        [identity("BITSET"),f("BITSET","quantity","12.5pcs",None,None,"pending_confirmation"),f("BITSET","material","s2钢"),f("BITSET","color","银色")])
textpack(D,9,"抛光机模板说明不能变证据","template_injection","POLISHER",
         "额定功率: 700W\n接口: 两脚插头\n仅为模板，净重: 0.01kg",
         [f("POLISHER","power",700,"W","rating:rated"),f("POLISHER","interface","两脚插头")])
package(D,10,"多功能机商品隔离表顺序颠倒","reordered_header",{"tools.csv":"额定功率,接口,SKU,净重\n350W,AC,MULTITOOL,1.4kg\n"},
        [identity("MULTITOOL"),f("MULTITOOL","power",350,"W","rating:rated"),f("MULTITOOL","interface","ac"),f("MULTITOOL","net_weight",1400,"g","net")])

# Domain holdout: do not execute these predictions until the development fixes freeze.
D = "adhesive"
textpack(D,1,"VHB4910 厚度与透明外观","public_tape","TAPE4910",
         "厚度: 1.0mm\n颜色: 透明\n胶系: 丙烯酸",
         [f("TAPE4910","custom:厚度","1.0mm"),f("TAPE4910","color","透明"),f("TAPE4910","custom:胶系","丙烯酸")],public_model="3M VHB 4910",note="Metric nominal thickness selected; not exact imperial conversion.")
textpack(D,2,"VHB4905 指定卷宽长度","public_tape","TAPE4905",
         "宽度: 203.2mm\n长度: 65.84m\n厚度: 0.5mm",
         [f("TAPE4905","width",203.2,"mm"),f("TAPE4905","length",65840,"mm"),f("TAPE4905","custom:厚度","0.5mm")],public_model="3M 7010535963",source="https://www.3m.com/3M/en_US/p/d/v100809126/")
textpack(D,3,"双面泡棉胶面积不是尺寸","tape_area","FOAMTAPE",
         "粘接面积: 25cm²\n厚度: 2mm\n颜色: 白色",
         [f("FOAMTAPE","custom:粘接面积","25cm²"),f("FOAMTAPE","custom:厚度","2mm"),f("FOAMTAPE","color","白色")])
package(D,4,"结构胶组分独立对象","adhesive_components",{"adhesive.json":json.dumps([{"SKU":"RESIN-A","净重":"750g","颜色":"白色"},{"SKU":"RESIN-B","净重":"250g","颜色":"黑色"}],ensure_ascii=False)},
        [identity("RESIN-A"),identity("RESIN-B"),f("RESIN-A","net_weight",750,"g","net"),f("RESIN-A","color","白色"),f("RESIN-B","net_weight",250,"g","net"),f("RESIN-B","color","黑色")])
textpack(D,5,"环氧胶混合比例不可当数字范围","mixing_ratio","EPOXY",
         "混合比例: 2:1\n适用期: 30min\n固化时间: 24h",
         [f("EPOXY","custom:混合比例","2:1"),f("EPOXY","custom:适用期","30min"),f("EPOXY","custom:固化时间","24h")])
package(D,6,"密封胶同值单位转换","sealant_volume",{"a.txt":"SKU: SEALANT\n净容量: 0.3L\n颜色: 灰色","b.txt":"SKU: SEALANT\n净容量: 300mL"},
        [identity("SEALANT"),f("SEALANT","capacity_volume",300,"mL"),f("SEALANT","color","灰色")])
textpack(D,7,"绝缘胶带短期长期耐温","tape_duration_condition","INSUTAPE",
         "长期耐温: 90°C\n短期耐温: 150°C\n宽度: 19mm",
         [f("INSUTAPE","custom:长期耐温","90°C"),f("INSUTAPE","custom:短期耐温","150°C"),f("INSUTAPE","width",19,"mm")])
package(D,8,"导热胶表格条件不是单位","adhesive_test_condition",{"spec.csv":"SKU,粘度(25°C),粘度(60°C),颜色\nTHERMALGLUE,4000cP,2000cP,白色\n"},
        [identity("THERMALGLUE"),f("THERMALGLUE","custom:粘度(25°c)","4000cP"),f("THERMALGLUE","custom:粘度(60°c)","2000cP"),f("THERMALGLUE","color","白色")])
textpack(D,9,"胶粘片限值与公差不同","adhesive_tolerance","SHEETGLUE",
         "长度: 不超过100mm\n宽度: 50±1mm\n颜色: 透明",
         [f("SHEETGLUE","length","≤100","mm"),f("SHEETGLUE","width","50±1","mm"),f("SHEETGLUE","color","透明")])
package(D,10,"包装胶带关键字值不得当表头","key_value_orientation",{"spec.csv":"参数,值\nSKU,PACKTAPE\n材质,聚丙烯\n宽度,48mm\n长度,100m\n"},
        [identity("PACKTAPE"),f("PACKTAPE","material","聚丙烯"),f("PACKTAPE","width",48,"mm"),f("PACKTAPE","length",100000,"mm")])

D = "metrology"
textpack(D,1,"Fluke117 外形与环境","public_meter","METER117",
         "产品尺寸: 167x84x46mm\n重量: 550g\n工作温度: -10~50°C",
         [f("METER117","dimensions",[167,84,46],"mm","product"),f("METER117","weight",550,"g","unspecified"),f("METER117","temperature",[-10,50],"°C","operating")],public_model="Fluke 117")
textpack(D,2,"Fluke62MAX 测温范围非工作温度","public_meter","IR62",
         "测温范围: -30~500°C\n重量: 255g\n防护等级: IP54",
         [f("IR62","custom:测温范围","-30~500°C"),f("IR62","weight",255,"g","unspecified"),f("IR62","ip_rating","ip54")],public_model="Fluke 62 MAX",source="https://www.fluke.com/en-us/product/temperature-measurement/ir-thermometers/fluke-62-max")
textpack(D,3,"卡尺分辨率与误差不同","metrology_error","CALIPER",
         "测量范围: 0~150mm\n分辨率: 0.01mm\n示值误差: ±0.02mm",
         [f("CALIPER","custom:测量范围","0~150mm"),f("CALIPER","custom:分辨率","0.01mm"),f("CALIPER","custom:示值误差","±0.02mm")])
package(D,4,"电子秤防止克和千克混淆","scale_conflict",{"a.txt":"SKU: SCALE\n重量: 2kg\n最大称量: 5kg","b.txt":"SKU: SCALE\n重量: 2100g\n最大称量: 5kg"},
        [identity("SCALE"),conflict("SCALE","weight",[[2000,"g"],[2100,"g"]],"unspecified"),f("SCALE","custom:最大称量","5kg")])
textpack(D,5,"温度记录仪华氏等价范围","fahrenheit_range","TEMPLOGGER",
         "工作温度: 32~104°F\n储存温度: -40~158°F\n接口: USB",
         [f("TEMPLOGGER","temperature",[0,40],"°C","operating"),f("TEMPLOGGER","temperature",[-40,70],"°C","storage"),f("TEMPLOGGER","interface","usb")])
package(D,6,"示波器单位列含括号参数","scope_unit_headers",{"scope.csv":"SKU,额定电压(V),净重(g),带宽(MHz)\nOSCILLOSCOPE,12,850,100\n"},
        [identity("OSCILLOSCOPE"),f("OSCILLOSCOPE","voltage",12,"V","rating:rated"),f("OSCILLOSCOPE","net_weight",850,"g","net"),f("OSCILLOSCOPE","custom:带宽","100 MHz")])
textpack(D,7,"频率计上下界与精度条件","frequency_limit","FREQCOUNTER",
         "最大频率: 10MHz\n典型频率: 1MHz\n精度: ±1ppm",
         [f("FREQCOUNTER","frequency",10000000,"Hz","rating:max"),f("FREQCOUNTER","frequency",1000000,"Hz","rating:typical"),f("FREQCOUNTER","custom:精度","±1ppm")])
package(D,8,"两个型号仪表无 SKU 归属","meter_model_identity",{"meter.json":json.dumps([{"型号":"DM-LOW","输入电压":"5V","接口":"USB"},{"型号":"DM-HIGH","输入电压":"12V","接口":"LAN"}],ensure_ascii=False)},
        [f(owner(model="DM-LOW"),"model","dm-low"),f(owner(model="DM-LOW"),"voltage",5,"V","input"),f(owner(model="DM-LOW"),"interface","usb"),f(owner(model="DM-HIGH"),"model","dm-high"),f(owner(model="DM-HIGH"),"voltage",12,"V","input"),f(owner(model="DM-HIGH"),"interface","lan")])
textpack(D,9,"声级计 A 与 C 计权","acoustic_weighting","SOUNDMETER",
         "A计权量程: 30~130dB\nC计权量程: 35~130dB\n输入电压: 9V",
         [f("SOUNDMETER","custom:a计权量程","30~130dB"),f("SOUNDMETER","custom:c计权量程","35~130dB"),f("SOUNDMETER","voltage",9,"V","input")])
textpack(D,10,"耐压仪负号不能被吞掉","signed_environment","HIPOT",
         "工作温度: −10~40°C\n输出电压: 0~5000V\n接口: RS232",
         [f("HIPOT","temperature",[-10,40],"°C","operating"),f("HIPOT","voltage",[0,5000],"V","output"),f("HIPOT","interface","rs232")])


def dataset():
    assert len(CASES) == 100 and len({c["id"] for c in CASES}) == 100
    assert all(sum(c["domain"] == d for c in CASES) == 10 for d in DOMAINS)
    return {"schema_version":1,"protocol":"See docs/TEST_PHASE_100.md; assistant-labelled, not population accuracy or independent blind data.",
            "gold_revision":"unit-case-v2-exposed-regression",
            "created_date":"2026-09-16","sample_unit":"distinct curated package",
            "facts_columns":["product_key","field","scope","fact_status","value_unit_pairs"],"cases":CASES}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset(),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
