"""
Điện thoại Android (máy thật) cho worker đăng bài, điều khiển từ PC qua ADB WiFi + uiautomator2:
kết nối (lại) khi rớt mạng, bật màn hình, đẩy ảnh vào thư viện ảnh, tìm / bấm nút theo chữ, dán chữ tiếng Việt,
chụp màn hình + cây giao diện khi lỗi (để sửa selector).

Cấu hình mỗi máy (NAME = PHONE1, PHONE2...) trong .env:
  {NAME}_ADDR   IP:cổng ADB WiFi (vd 192.168.1.21:5555), hoặc chỉ IP (tự dò cổng "Gỡ lỗi không dây" qua mDNS,
                máy phải ghép đôi trước bằng: adb pair IP:cổng mã), hoặc serial USB.
Máy cần: tắt khoá màn hình bảo mật (PIN / vân tay), bật "Gỡ lỗi không dây", đăng nhập sẵn các app.
"""

import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import adbutils
import uiautomator2 as u2

from .config import env, human_delay, log
from .errors import JobError

# Ảnh của job đẩy vào đây (thư mục riêng, xoá sạch trước mỗi job và sau khi đăng xong)
REMOTE_DIR = "/storage/emulated/0/Pictures/A51"
MEDIA_URI = "content://media/external/images/media"
CONNECT_TIMEOUT = 10   # Giây chờ adb connect
MDNS_TIMEOUT = 15      # Giây dò cổng ADB WiFi qua mDNS
SCAN_TIMEOUT = 20      # Giây chờ thư viện ảnh nhận ảnh vừa đẩy
APP_START_TIMEOUT = 20

# Popup lạ (gợi ý, khảo sát, bật thông báo...): chỉ bấm các nút KHÔNG làm mất bài đang soạn
DISMISS_TEXTS = ["Not now", "Not Now", "Lúc khác", "Để sau", "Không phải bây giờ", "Skip", "Bỏ qua",
                 "No thanks", "Không, cảm ơn", "Dismiss", "Later"]
# Hộp thoại xin quyền của Android: quyền cần thiết đã cấp sẵn (pm grant) nên quyền nào app còn hỏi (danh bạ, vị trí...)
# đều TỪ CHỐI (chỉ bấm trong app quản lý quyền của hệ thống)
PERMISSION_PACKAGE_REGEX = r".*permissioncontroller.*|.*packageinstaller.*"
PERMISSION_DENY_TEXTS = ["Không cho phép", "Don't allow", "Deny"]
KEYGUARD_WINDOWS = ["NotificationShade", "Keyguard"]


def texts_regex(texts, contains=False):
    """Regex (Java, không phân biệt hoa thường) khớp một trong texts: cả chuỗi, hoặc chứa (contains)."""
    body = "|".join(re.escape(t) for t in texts)
    return f"(?is).*(?:{body}).*" if contains else f"(?is)\\s*(?:{body})\\s*"


def adb_path():
    return adbutils.adb_path()


def adb(*args, timeout=30):
    result = subprocess.run([adb_path(), *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout)
    return (result.stdout or "") + (result.stderr or "")


class Phone:
    """Một điện thoại trong .env ({NAME}_ADDR). get(): Device đang kết nối, rớt thì kết nối lại."""

    def __init__(self, name):
        self.name = name.upper()
        self.addr = env(f"{self.name}_ADDR", "").strip()
        self.device = None

    def get(self):
        if self.device is not None and self.device.alive():
            return self.device
        if not self.addr:
            raise SystemExit(f"Chưa đặt {self.name}_ADDR trong .env (IP:cổng ADB WiFi của máy, vd 192.168.1.21:5555).")
        serial = self.connect_serial()
        log(f"ĐANG KẾT NỐI {self.name} ({serial})...")
        try:
            self.device = Device(serial, self.name)
        except Exception as e:
            raise JobError("DEVICE_OFFLINE", f"Không điều khiển được {self.name} ({serial}): {e}")
        log(f"ĐÃ KẾT NỐI {self.name}: {self.device.model}")
        return self.device

    def connect_serial(self):
        """
        adb connect tới máy, trả về serial. Chỉ có IP (hoặc cổng cũ hết hiệu lực): dò cổng qua mDNS
        (adb mới khởi động cần vài giây mới thấy máy, nên dò lại tới MDNS_TIMEOUT giây).
        """
        adb("start-server")
        ip = self.addr.split(":")[0]
        is_ip = re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ip) is not None
        online = self.online_serials()
        if self.addr in online:
            return self.addr
        if is_ip:
            # Đã nối sẵn tới IP này (cổng khác cổng trong .env)
            for serial in sorted(online):
                if serial.startswith(ip + ":"):
                    return serial
        if ":" in self.addr and self.try_connect(self.addr):
            return self.addr
        if is_ip:
            deadline = time.time() + MDNS_TIMEOUT
            while True:
                for name, found in self.mdns_services(ip):
                    if self.try_connect(found):
                        log(f"{self.name}: dò được cổng ADB WiFi {found}")
                        return found
                    # adb tự nối qua mDNS: serial là tên dịch vụ, vd adb-XXXX._adb-tls-connect._tcp
                    for serial in self.online_serials():
                        if serial.startswith(name + "."):
                            return serial
                if time.time() >= deadline:
                    break
                time.sleep(2)
        raise JobError("DEVICE_OFFLINE",
                       f"Không kết nối được {self.name} ({self.addr}): máy tắt / khác WiFi / tắt Gỡ lỗi không dây, "
                       f"hoặc chưa ghép đôi (adb pair IP:cổng mã)")

    @staticmethod
    def online_serials():
        try:
            return {d.serial for d in adbutils.adb.device_list()}
        except Exception:
            return set()

    def try_connect(self, addr):
        try:
            adbutils.adb.connect(addr, timeout=CONNECT_TIMEOUT)
        except Exception as e:
            log(f"{self.name}: adb connect {addr} lỗi: {e}")
            return False
        return addr in self.online_serials()

    @staticmethod
    def mdns_services(ip):
        """(tên, IP:cổng) "Gỡ lỗi không dây" (cổng đổi mỗi lần bật lại) của máy có IP ip, từ adb mdns services."""
        output = adb("mdns", "services")
        return [(name, f"{host}:{port}") for name, host, port in
                re.findall(r"^(\S+)\s+_adb-tls-connect\._tcp\.?\s+(\d+\.\d+\.\d+\.\d+):(\d+)", output, flags=re.M)
                if host == ip]


class Device:
    """Thao tác trên điện thoại qua uiautomator2 (self.d)."""

    def __init__(self, serial, name):
        self.serial = serial
        self.name = name
        self.d = u2.connect(serial)
        try:
            info = self.d.info
        except Exception as e:
            # Server uiautomator trên máy bị tắt (HyperOS hay dọn tiến trình nền): khởi động lại 1 lần
            log(f"{name}: uiautomator không phản hồi ({e}), khởi động lại...")
            self.d.reset_uiautomator()
            info = self.d.info  # Vẫn lỗi: máy không phản hồi
        self.model = f"{info.get('productName') or ''} (Android SDK {info.get('sdkInt')})".strip()

    def alive(self):
        try:
            self.d.info
            return True
        except Exception:
            return False

    def shell(self, args, timeout=60):
        return self.d.shell(args, timeout=timeout).output or ""

    # ---- Màn hình / app ----

    def wake(self):
        """Bật màn hình, đang ở màn hình khoá thì vuốt lên. Còn khoá bảo mật (PIN / vân tay) thì báo lỗi."""
        self.d.screen_on()
        time.sleep(1)
        if self.keyguard_showing():
            self.d.swipe(0.5, 0.85, 0.5, 0.2)
            time.sleep(1.5)
        if self.keyguard_showing() or "deviceLocked=1" in self.shell("dumpsys trust"):
            raise JobError("DEVICE_LOCKED", f"{self.name} đang khoá màn hình bảo mật: tắt PIN / vân tay cho máy chạy worker")

    def keyguard_showing(self):
        """Màn hình khoá đang hiện: cửa sổ đang focus là màn hình khoá (cờ isKeyguardShowing của HyperOS báo sai)."""
        focus = self.shell("dumpsys window | grep mCurrentFocus")
        return any(marker in focus for marker in KEYGUARD_WINDOWS)

    def start_app(self, package, error_prefix):
        """Mở app từ đầu (tắt phiên cũ: bỏ bài đang soạn dở của job trước)."""
        if not self.shell(["pm", "path", package]).strip():
            raise JobError(f"{error_prefix}_APP_NOT_INSTALLED", f"{self.name} chưa cài app {package}")
        self.d.app_start(package, stop=True)
        if not self.d.app_wait(package, front=True, timeout=APP_START_TIMEOUT):
            raise JobError(f"{error_prefix}_APP_NOT_STARTED", f"Không mở được app {package} trên {self.name}")
        time.sleep(4)

    def grant(self, package, permissions):
        """Cấp sẵn quyền (đọc ảnh, thông báo) để app không hỏi giữa chừng. Quyền không có trên bản Android này thì bỏ qua."""
        for permission in permissions:
            self.shell(["pm", "grant", package, permission])

    def app_version(self, package):
        match = re.search(r"versionName=(\S+)", self.shell(["dumpsys", "package", package]))
        return match.group(1) if match else "?"

    def home(self):
        self.d.press("home")

    def hide_keyboard(self):
        """Đang hiện bàn phím thì bấm Back để ẩn (không hiện thì không bấm: Back sẽ thoát màn hình)."""
        if "mInputShown=true" in self.shell("dumpsys input_method"):
            self.back()

    def back(self):
        self.d.press("back")
        time.sleep(1)

    # ---- Tìm / bấm ----

    def find(self, texts=None, descs=None, res=None, timeout=0, contains=False, **extra):
        """
        Phần tử đầu tiên khớp: chữ (texts), mô tả (descs, content-desc) hoặc resource-id (res, regex).
        Chờ tối đa timeout giây. Không thấy -> None.
        """
        selectors = []
        if texts:
            selectors.append(dict(textMatches=texts_regex(texts, contains), **extra))
        if descs:
            selectors.append(dict(descriptionMatches=texts_regex(descs, contains), **extra))
        if res:
            selectors.append(dict(resourceIdMatches=res, **extra))
        deadline = time.time() + timeout
        while True:
            for selector in selectors:
                element = self.d(**selector)
                if element.exists:
                    return element
            if time.time() >= deadline:
                return None
            time.sleep(1)

    def click(self, element, label=""):
        human_delay()
        element.click()
        if label:
            log(f"ĐÃ BẤM: {label}")
        time.sleep(1)

    def tap(self, label="", **find_args):
        """Tìm rồi bấm. Trả về True nếu đã bấm."""
        element = self.find(**find_args)
        if element is None:
            return False
        self.click(element, label)
        return True

    def screen_text(self):
        """Toàn bộ chữ + content-desc trên màn hình (chữ thường), để dò thông báo."""
        try:
            root = ET.fromstring(self.d.dump_hierarchy())
        except Exception:
            return ""
        parts = []
        for node in root.iter("node"):
            for key in ("text", "content-desc"):
                value = node.get(key)
                if value:
                    parts.append(value)
        return "\n".join(parts).lower()

    def screen_has(self, texts):
        text = self.screen_text()
        return any(t.lower() in text for t in texts)

    def dismiss_popups(self, extra_texts=(), rounds=3):
        """Đóng popup lạ / từ chối quyền app xin thêm. Trả về số popup đã đóng."""
        closed = 0
        for _ in range(rounds):
            deny = self.find(texts=PERMISSION_DENY_TEXTS, packageNameMatches=PERMISSION_PACKAGE_REGEX)
            element = deny or self.find(texts=[*DISMISS_TEXTS, *extra_texts])
            if element is None:
                break
            try:
                label = element.info.get("text") or "popup"
                self.click(element, f"đóng popup '{label}'")
            except Exception:
                pass  # Popup tự biến mất trước khi bấm
            closed += 1
        return closed

    def input_text(self, element, text):
        """Điền text (tiếng Việt, xuống dòng) vào ô nhập. Trả về chữ đang có trong ô sau khi điền."""
        self.click(element)
        element.set_text(text)
        time.sleep(1)
        actual = element.get_text() or ""
        if normalize(actual) != normalize(text):
            # set_text không nhận (ô nhập tự vẽ): xoá rồi dán qua clipboard
            element.clear_text()
            self.d.send_keys(text)
            time.sleep(1)
            actual = element.get_text() or ""
        return actual

    # ---- Ảnh ----

    def push_images(self, paths):
        """
        Đẩy ảnh vào REMOTE_DIR và chờ thư viện ảnh nhận. Ảnh 01 là MỚI NHẤT (ô đầu tiên trong thư viện của app),
        ảnh sau cũ dần: chọn lần lượt ô 1..N là đúng thứ tự bài.
        """
        self.clear_images()
        now = time.time()
        remote = []
        for i, path in enumerate(paths):
            os.utime(path, (now - i * 60, now - i * 60))  # adb push giữ giờ sửa file
            target = f"{REMOTE_DIR}/a51_{int(now)}_{i + 1:02d}.jpg"
            self.d.push(path, target)
            remote.append(target)
        # Quét từ ảnh cuối lên ảnh đầu, cách nhau > 1 giây: ngày thêm vào thư viện cũng đúng thứ tự
        for target in reversed(remote):
            self.scan(target)
            time.sleep(1.2)
        deadline = time.time() + SCAN_TIMEOUT
        while self.indexed_count() < len(remote):
            if time.time() >= deadline:
                raise JobError("DEVICE_MEDIA_SCAN_FAILED",
                               f"Thư viện ảnh của {self.name} chưa nhận {len(remote)} ảnh sau {SCAN_TIMEOUT} giây")
            time.sleep(1)
        log(f"ĐÃ ĐẨY {len(remote)} ẢNH vào {self.name} ({REMOTE_DIR})")
        return remote

    def scan(self, path):
        uri = f"file://{path}"
        for package in self.media_packages():
            self.shell(["am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", uri, "-p", package])

    def media_packages(self):
        packages = re.findall(r"package:(\S*providers\.media\S*)", self.shell(["pm", "list", "packages", "media"]))
        return packages or ["com.android.providers.media"]

    def indexed_count(self):
        output = self.shell(["content", "query", "--uri", MEDIA_URI, "--projection", "_id",
                             "--where", f"_data LIKE '{REMOTE_DIR}/%'"])
        return len(re.findall(r"^Row:", output, flags=re.M))

    def clear_images(self):
        """Xoá ảnh của job trước khỏi máy và khỏi thư viện ảnh."""
        self.shell(["content", "delete", "--uri", MEDIA_URI, "--where", f"_data LIKE '{REMOTE_DIR}/%'"])
        self.shell(["rm", "-rf", REMOTE_DIR])
        self.shell(["mkdir", "-p", REMOTE_DIR])

    # ---- Ghi lại màn hình ----

    def capture(self, folder, name):
        """Chụp màn hình (.png) + cây giao diện (.xml, để sửa selector). Trả về đường dẫn không đuôi."""
        os.makedirs(folder, exist_ok=True)
        base = os.path.join(folder, f"{name}-{datetime.now():%Y%m%d-%H%M%S}")
        self.d.screenshot(base + ".png")
        with open(base + ".xml", "w", encoding="utf-8") as f:
            f.write(self.d.dump_hierarchy(pretty=True))
        return base


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip()
