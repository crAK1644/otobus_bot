from datetime import datetime

from iett_bot.domain import Line
from iett_bot.schedule import ISTANBUL

TEXT = {
    "en": {
        "welcome": "Welcome! Choose a language, then add your bus lines with /lines. "
        "You can follow several lines. Use /status to check their announcements now.\n"
        "I forward İETT service notices when I detect them. Notices may be published late or "
        "disappear between checks. This is an independent community project.",
        "choose_line": "Send a line code or name (for example 59RH).",
        "no_results": "No matching lines. Try the exact line code.",
        "choose_result": "Choose a line to follow:",
        "subscribed": "Following {code}. I’ll check for current announcements now.",
        "already_subscribed": "You already follow {code}.",
        "limit": "You can follow up to {limit} lines. Remove one first.",
        "removed": "Unfollowed {code}.",
        "lines": "Your lines (notification hours use Istanbul time):",
        "multiple_lines": "Follow up to {limit} lines. Use ‘Add a line’ again for each one.",
        "check_line": "Check {code}",
        "check_now": "Check now",
        "no_lines": "You haven’t added any lines. Use /lines to get started.",
        "add": "Add a line",
        "remove": "Remove {code}",
        "hours": "Hours · {code}",
        "settings": "Choose your language below. To set notification hours, choose a line.\n"
        "Monitoring continues around the clock, including while notifications are paused.",
        "language_set": "Language saved: English.",
        "schedule_prompt": "Notification hours for {code} (Istanbul time).\n"
        "Send: 1,2,3,4,5 07:00-10:00,15:00-19:00\n"
        "Days: 1=Monday … 7=Sunday; up to four windows. Overnight windows belong to "
        "their starting day. Send ‘all’ for every day, all day.\n"
        "Or use /settings {code} 1,2,3,4,5 07:00-10:00",
        "schedule_saved": "Hours saved for {code}: {schedule}",
        "bad_schedule": "Invalid hours. Example: 1,2,3,4,5 07:00-10:00,15:00-19:00\n"
        "Use days 1–7, HH:MM, or ‘all’.",
        "not_subscribed": "Add this line with /lines first.",
        "paused": "Notifications paused. Monitoring and saving announcements continue. "
        "Use /resume to receive alerts again.",
        "resumed": "Notifications resumed. Current notices will follow your selected hours.",
        "delete_prompt": "Delete your Telegram identifier, subscriptions, settings, and delivery "
        "records? Shared public İETT announcement history is retained.",
        "delete_confirm": "Delete my data",
        "deleted": "Your personal data has been deleted. Use /start to subscribe again.",
        "cancel": "Cancel",
        "cancelled": "Cancelled.",
        "source_error": "İETT could not be checked right now. Try again shortly.",
        "unexpected_error": "Something went wrong. Please try again shortly.",
        "status_heading": "Current announcements",
        "checking": "Checking İETT announcements…",
        "refresh_failed": "⚠ I couldn’t refresh this line right now; its current situation is "
        "unknown. Any results below are from the last successful check.",
        "notice_count": "Announcements in the latest successful check: {count}.",
        "notice_delivery": "Alerts follow your notification hours and pause setting. "
        "Use ‘Check now’ or /status to read the announcements here at any time.",
        "last_check": "Last successful check: {time}",
        "not_checked": "Waiting for the first successful check.",
        "stale": "⚠ Source data is stale; the current situation is unknown.",
        "no_notices": "No notices in the last successful response. This does not guarantee "
        "that every departure is running.",
        "paused_status": "Notifications are currently paused.",
        "notice": "İETT service announcement",
        "detected": "First detected: {time} (Istanbul)",
        "source": "Official source: {url}",
        "outage": "⚠ Announcement monitoring is temporarily unavailable for this line. "
        "The latest situation is unknown.\nUnavailability detected: {time} (Istanbul)",
        "recovery": "Announcement monitoring is available again for this line. "
        "This is a monitoring update, not confirmation that bus service has resumed.\n"
        "Checked: {time} (Istanbul)",
        "help": "/lines — add or remove lines\n/settings — language and notification hours\n"
        "/status — check announcements for all your lines now\n"
        "/status 59RH — check one of your lines\n/pause — pause alerts\n/resume — resume alerts\n"
        "/delete_me — delete your data\n/cancel — cancel the current step\n\n"
        "Alerts contain İETT’s original Turkish text. A notice disappearing does not mean "
        "service has resumed. Unchanged notices are not repeated daily.",
    },
    "tr": {
        "welcome": "Hoş geldiniz! Dilinizi seçin, ardından /lines ile otobüs hatlarınızı ekleyin. "
        "Birden fazla hat takip edebilirsiniz. Güncel duyurular için /status kullanın.\n"
        "İETT hizmet duyurularını gördüğümde size iletirim. Duyurular geç yayımlanabilir veya "
        "kontroller arasında kaldırılabilir. Bu bağımsız bir topluluk projesidir.",
        "choose_line": "Hat kodu veya adı gönderin (örneğin 59RH).",
        "no_results": "Eşleşen hat bulunamadı. Tam hat kodunu deneyin.",
        "choose_result": "Takip etmek istediğiniz hattı seçin:",
        "subscribed": "{code} takip ediliyor. Güncel duyuruları şimdi kontrol edeceğim.",
        "already_subscribed": "{code} hattını zaten takip ediyorsunuz.",
        "limit": "En fazla {limit} hat takip edebilirsiniz. Önce bir hattı kaldırın.",
        "removed": "{code} takibi kaldırıldı.",
        "lines": "Hatlarınız (bildirim saatleri İstanbul saatidir):",
        "multiple_lines": "En fazla {limit} hat takip edebilirsiniz. Her hat için yeniden "
        "‘Hat ekle’ düğmesini kullanın.",
        "check_line": "{code} kontrol et",
        "check_now": "Şimdi kontrol et",
        "no_lines": "Henüz hat eklemediniz. /lines ile başlayabilirsiniz.",
        "add": "Hat ekle",
        "remove": "{code} kaldır",
        "hours": "Saatler · {code}",
        "settings": "Aşağıdan dilinizi seçin. Bildirim saatleri için bir hat seçin.\n"
        "Bildirimler duraklatıldığında da duyurular gün boyu izlenip kaydedilir.",
        "language_set": "Dil kaydedildi: Türkçe.",
        "schedule_prompt": "{code} bildirim saatleri (İstanbul saati).\n"
        "Örnek: 1,2,3,4,5 07:00-10:00,15:00-19:00\n"
        "Günler: 1=Pazartesi … 7=Pazar; en fazla dört saat aralığı. Gece yarısını aşan "
        "aralıklar başladıkları güne aittir. Her gün, tüm gün için ‘all’ gönderin.\n"
        "Alternatif: /settings {code} 1,2,3,4,5 07:00-10:00",
        "schedule_saved": "{code} saatleri kaydedildi: {schedule}",
        "bad_schedule": "Geçersiz saatler. Örnek: 1,2,3,4,5 07:00-10:00,15:00-19:00\n"
        "1–7 günlerini, SS:DD biçimini veya ‘all’ kullanın.",
        "not_subscribed": "Önce /lines ile bu hattı ekleyin.",
        "paused": "Bildirimler duraklatıldı. Duyurular izlenip kaydedilmeye devam ediyor. "
        "Bildirimleri açmak için /resume kullanın.",
        "resumed": "Bildirimler açıldı. Güncel duyurular seçtiğiniz saatlerde iletilecek.",
        "delete_prompt": "Telegram kimliğiniz, abonelikleriniz, ayarlarınız ve gönderim "
        "kayıtlarınız silinsin mi? Ortak, herkese açık İETT duyuru geçmişi korunur.",
        "delete_confirm": "Verilerimi sil",
        "deleted": "Kişisel verileriniz silindi. Yeniden abone olmak için /start kullanın.",
        "cancel": "Vazgeç",
        "cancelled": "İptal edildi.",
        "source_error": "İETT şu anda kontrol edilemiyor. Biraz sonra tekrar deneyin.",
        "unexpected_error": "Bir sorun oluştu. Lütfen biraz sonra tekrar deneyin.",
        "status_heading": "Güncel duyurular",
        "checking": "İETT duyuruları kontrol ediliyor…",
        "refresh_failed": "⚠ Bu hat şu anda yeniden kontrol edilemedi; güncel durum bilinmiyor. "
        "Aşağıdaki sonuçlar varsa son başarılı kontrolden alınmıştır.",
        "notice_count": "Son başarılı kontroldeki duyuru sayısı: {count}.",
        "notice_delivery": "Bildirimler seçtiğiniz saatlere ve duraklatma ayarınıza göre iletilir. "
        "Duyuruları istediğiniz zaman okumak için ‘Şimdi kontrol et’ veya /status kullanın.",
        "last_check": "Son başarılı kontrol: {time}",
        "not_checked": "İlk başarılı kontrol bekleniyor.",
        "stale": "⚠ Kaynak verisi güncel değil; mevcut durum bilinmiyor.",
        "no_notices": "Son başarılı yanıtta duyuru yok. Bu, tüm seferlerin çalıştığı "
        "anlamına gelmez.",
        "paused_status": "Bildirimler şu anda duraklatılmış durumda.",
        "notice": "İETT hizmet duyurusu",
        "detected": "İlk görülme: {time} (İstanbul)",
        "source": "Resmî kaynak: {url}",
        "outage": "⚠ Bu hat için duyuru takibi geçici olarak yapılamıyor. "
        "Güncel durum bilinmiyor.\nErişim sorunu başlangıcı: {time} (İstanbul)",
        "recovery": "Bu hat için duyuru takibi yeniden çalışıyor. Bu, takip sisteminin "
        "durumudur; otobüs seferlerinin yeniden başladığı anlamına gelmez.\n"
        "Kontrol: {time} (İstanbul)",
        "help": "/lines — hat ekle veya kaldır\n/settings — dil ve bildirim saatleri\n"
        "/status — tüm hatlarınızın duyurularını şimdi kontrol et\n"
        "/status 59RH — takip ettiğiniz bir hattı kontrol et\n/pause — bildirimleri duraklat\n"
        "/resume — bildirimleri aç\n/delete_me — verilerini sil\n/cancel — işlemi iptal et\n\n"
        "Duyurular İETT’nin özgün Türkçe metniyle iletilir. Bir duyurunun kaldırılması, "
        "seferin yeniden başladığı anlamına gelmez. Aynı duyuru her gün tekrar gönderilmez.",
    },
}


def tr(language: str, key: str, **values: object) -> str:
    return TEXT.get(language, TEXT["tr"])[key].format(**values)


def local_time(value: datetime) -> str:
    return value.astimezone(ISTANBUL).strftime("%d.%m.%Y %H:%M")


def split_text(text: str, limit: int = 3800) -> list[str]:
    """Telegram's limit counts UTF-16 units, including surrogate pairs in emoji."""
    if not text:
        return [""]
    parts: list[str] = []
    buffer: list[str] = []
    length = 0
    for char in text:
        units = 2 if ord(char) > 0xFFFF else 1
        if length + units > limit:
            parts.append("".join(buffer))
            buffer, length = [], 0
        buffer.append(char)
        length += units
    if buffer:
        parts.append("".join(buffer))
    return parts


def notice_messages(line: Line, text: str, first_seen: datetime, language: str) -> list[str]:
    header = f"🚍 {line.code} — {line.name}\n{tr(language, 'notice')}\n\n"
    footer = (
        f"\n\n{tr(language, 'detected', time=local_time(first_seen))}\n"
        f"{tr(language, 'source', url=line.url)}"
    )
    allowance = 4000 - len((header + footer).encode("utf-16-le")) // 2
    return [header + part + footer for part in split_text(text, max(100, allowance))]


def incident_messages(line: Line, kind: str, at: datetime, language: str) -> list[str]:
    text = f"🚍 {line.code} — {line.name}\n\n{tr(language, kind, time=local_time(at))}"
    return split_text(text + "\n\n" + tr(language, "source", url=line.url))
