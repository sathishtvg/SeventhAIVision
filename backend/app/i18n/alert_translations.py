"""Alert code translation templates.

Keys are alert_code values stored on alerts/incidents rows.
Values are Python format strings accepting **message_params kwargs.
Fallback: English, then raw alert_code if no template found.
"""

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # LPR
        "lpr.blocklist_hit":      "License plate {plate} matched the blocklist (confidence {confidence:.0%})",
        "lpr.allowlist_hit":      "License plate {plate} matched the allowlist",
        # Face
        "face.blocklist_hit":     "Blocked person detected (confidence {confidence:.0%})",
        "face.allowlist_hit":     "Known person detected: {name}",
        "face.unrecognized":      "Unrecognized face detected",
        # Intrusion
        "intrusion.zone_breach":  "Zone breach detected in {zone_name}",
        # Camera health
        "camera.offline":         "Camera went offline",
        "camera.tampering":       "Camera tampering detected",
        # PPE
        "ppe.violation":          "PPE violation: {missing_items} missing",
        # Crowd
        "crowd.density_high":     "Crowd density exceeded threshold ({count} persons)",
        # Fire/Smoke
        "fire_smoke.detected":    "Fire or smoke detected",
        # Weapon
        "weapon.detected":        "Weapon detected",
        # Behavior
        "behavior.loitering":     "Loitering detected ({duration}s)",
        "behavior.fighting":      "Fighting or aggression detected",
        "behavior.abandoned_object": "Abandoned object detected",
        "behavior.slip_fall":     "Slip or fall detected",
        # Alarm
        "alarm.zone_alarm":       "Alarm triggered in zone {zone_name}",
        # Access control
        "access.forced_entry":    "Forced entry detected at {door_name}",
        "access.unauthorized":    "Unauthorized access attempt at {door_name}",
        # Visitor
        "visitor.overstay":       "Visitor {visitor_name} has overstayed by {minutes} minutes",
    },
    "zh": {
        "lpr.blocklist_hit":      "车牌 {plate} 匹配黑名单（置信度 {confidence:.0%}）",
        "lpr.allowlist_hit":      "车牌 {plate} 匹配白名单",
        "face.blocklist_hit":     "检测到被拦截人员（置信度 {confidence:.0%}）",
        "face.allowlist_hit":     "检测到已知人员：{name}",
        "face.unrecognized":      "检测到未识别人脸",
        "intrusion.zone_breach":  "在 {zone_name} 检测到区域入侵",
        "camera.offline":         "摄像头已离线",
        "camera.tampering":       "检测到摄像头被篡改",
        "ppe.violation":          "PPE违规：缺少 {missing_items}",
        "crowd.density_high":     "人群密度超过阈值（{count} 人）",
        "fire_smoke.detected":    "检测到火灾或烟雾",
        "weapon.detected":        "检测到武器",
        "behavior.loitering":     "检测到徘徊行为（{duration} 秒）",
        "behavior.fighting":      "检测到打斗或攻击行为",
        "behavior.abandoned_object": "检测到遗留物品",
        "behavior.slip_fall":     "检测到滑倒或跌倒",
        "alarm.zone_alarm":       "区域 {zone_name} 触发警报",
        "access.forced_entry":    "在 {door_name} 检测到强行进入",
        "access.unauthorized":    "在 {door_name} 检测到未授权访问",
        "visitor.overstay":       "访客 {visitor_name} 已超时 {minutes} 分钟",
    },
    "ms": {
        "lpr.blocklist_hit":      "Plat nombor {plate} sepadan senarai hitam (keyakinan {confidence:.0%})",
        "lpr.allowlist_hit":      "Plat nombor {plate} sepadan senarai putih",
        "face.blocklist_hit":     "Orang disekat dikesan (keyakinan {confidence:.0%})",
        "face.allowlist_hit":     "Orang dikenali dikesan: {name}",
        "face.unrecognized":      "Wajah tidak dikenali dikesan",
        "intrusion.zone_breach":  "Pencerobohan zon dikesan di {zone_name}",
        "camera.offline":         "Kamera telah luar talian",
        "camera.tampering":       "Gangguan kamera dikesan",
        "ppe.violation":          "Pelanggaran PPE: {missing_items} tiada",
        "crowd.density_high":     "Kepadatan orang ramai melebihi ambang ({count} orang)",
        "fire_smoke.detected":    "Kebakaran atau asap dikesan",
        "weapon.detected":        "Senjata dikesan",
        "behavior.loitering":     "Pelesiran dikesan ({duration}s)",
        "behavior.fighting":      "Pergaduhan atau pencerobohan dikesan",
        "behavior.abandoned_object": "Objek tertinggal dikesan",
        "behavior.slip_fall":     "Terpeleset atau jatuh dikesan",
        "alarm.zone_alarm":       "Penggera dicetuskan di zon {zone_name}",
        "access.forced_entry":    "Kemasukan paksa dikesan di {door_name}",
        "access.unauthorized":    "Percubaan akses tanpa kebenaran di {door_name}",
        "visitor.overstay":       "Pelawat {visitor_name} telah melebihi masa {minutes} minit",
    },
    "ta": {
        "lpr.blocklist_hit":      "தகடு {plate} கருப்புப் பட்டியலில் பொருந்தியது (நம்பகத்தன்மை {confidence:.0%})",
        "lpr.allowlist_hit":      "தகடு {plate} வெள்ளைப் பட்டியலில் பொருந்தியது",
        "face.blocklist_hit":     "தடுக்கப்பட்ட நபர் கண்டறியப்பட்டார் (நம்பகத்தன்மை {confidence:.0%})",
        "face.allowlist_hit":     "அறிமுகமான நபர் கண்டறியப்பட்டார்: {name}",
        "face.unrecognized":      "அடையாளம் தெரியாத முகம் கண்டறியப்பட்டது",
        "intrusion.zone_breach":  "{zone_name} இல் அடித்தள மீறல் கண்டறியப்பட்டது",
        "camera.offline":         "கேமரா ஆஃப்லைன் ஆனது",
        "camera.tampering":       "கேமரா சேதம் கண்டறியப்பட்டது",
        "ppe.violation":          "PPE மீறல்: {missing_items} இல்லை",
        "crowd.density_high":     "கூட்ட அடர்த்தி வரம்பை மீறியது ({count} நபர்கள்)",
        "fire_smoke.detected":    "தீ அல்லது புகை கண்டறியப்பட்டது",
        "weapon.detected":        "ஆயுதம் கண்டறியப்பட்டது",
        "behavior.loitering":     "அலைந்திரிதல் கண்டறியப்பட்டது ({duration}s)",
        "behavior.fighting":      "சண்டை அல்லது ஆக்கிரமிப்பு கண்டறியப்பட்டது",
        "behavior.abandoned_object": "கைவிடப்பட்ட பொருள் கண்டறியப்பட்டது",
        "behavior.slip_fall":     "நழுவல் அல்லது வீழ்ச்சி கண்டறியப்பட்டது",
        "alarm.zone_alarm":       "{zone_name} மண்டலத்தில் அலாரம் கண்டறியப்பட்டது",
        "access.forced_entry":    "{door_name} இல் வலுக்கட்டாயமாக நுழைவு கண்டறியப்பட்டது",
        "access.unauthorized":    "{door_name} இல் அனுமதியற்ற அணுகல் முயற்சி",
        "visitor.overstay":       "பார்வையாளர் {visitor_name} {minutes} நிமிடங்கள் அதிகமாக தங்கியுள்ளார்",
    },
}

SUPPORTED_LOCALES: list[str] = list(TRANSLATIONS.keys())
FALLBACK_LOCALE = "en"


def translate_alert(alert_code: str, message_params: dict, locale: str = "en") -> str:
    """Return a rendered alert message for the given locale.

    Falls back to English if the locale is unsupported or the alert_code has
    no translation.  Falls back to the raw alert_code string as a last resort.
    """
    locale_map = TRANSLATIONS.get(locale, TRANSLATIONS[FALLBACK_LOCALE])
    template = locale_map.get(alert_code) or TRANSLATIONS[FALLBACK_LOCALE].get(alert_code)
    if template is None:
        return alert_code
    try:
        return template.format(**message_params)
    except (KeyError, ValueError):
        return template
