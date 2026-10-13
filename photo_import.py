"""用户选用照片：本机解码、公共图片网址下载；不调用 AI 或携带云端凭据。"""
import base64
import http.client
import io
import ipaddress
import socket
import ssl
import zlib
from urllib.parse import quote, urljoin, urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

register_heif_opener()
MAX_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 40 * 1024 * 1024
MAX_BATCH_COUNT = 50
MAX_PIXELS = 50_000_000


class PhotoError(ValueError):
    pass


def normalize(raw, source, name=""):
    if not raw or len(raw) > MAX_BYTES:
        raise PhotoError("照片不能为空，单张照片请控制在 20 MB 以内")
    try:
        with Image.open(io.BytesIO(raw)) as picture:
            if picture.format not in {"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF", "HEIF", "AVIF"}:
                raise PhotoError("请使用 JPG、PNG、WebP、HEIC、BMP、TIFF 或 GIF 照片")
            if picture.width * picture.height > MAX_PIXELS or max(picture.size) > 16384:
                raise PhotoError("照片分辨率过大，请先缩小到 5000 万像素、单边 16384 像素以内")
            animated = getattr(picture, "n_frames", 1) > 1
            picture.seek(0)
            picture = ImageOps.exif_transpose(picture).convert("RGBA")
            background = Image.new("RGB", picture.size, "white")
            background.paste(picture, mask=picture.getchannel("A"))
            output = io.BytesIO()
            background.save(output, format="PNG")
            return output.getvalue(), {"provider": source, "dimensions": list(background.size),
                                       "source_name": name[:120], "first_frame": animated}
    except PhotoError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, SyntaxError):
        raise PhotoError("照片无法读取或已损坏；请换一张 JPG、PNG、WebP 或 HEIC 照片") from None


def local_photo(data, name=""):
    if not isinstance(data, str) or len(data) > (MAX_BYTES + 2) // 3 * 4:
        raise PhotoError("单张照片请控制在 20 MB 以内")
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError:
        raise PhotoError("本机照片上传数据无效，请重新选择文件") from None
    # 不使用用户的文件名作为磁盘路径。
    return normalize(raw, "upload", str(name).replace("\\", "/").rsplit("/", 1)[-1])


def local_photos(data):
    """先验证整批照片，全部成功才允许替换任务素材；兼容旧单图请求。"""
    photos = data.get("photos") if "photos" in data else [data]
    if not isinstance(photos, list) or not photos or len(photos) > MAX_BATCH_COUNT:
        raise PhotoError("请至少选择一张本机照片，每批最多 50 张")
    total = 0
    for item in photos:
        if not isinstance(item, dict) or not isinstance(item.get("photo_data"), str):
            raise PhotoError("本机照片上传数据无效，请重新选择文件")
        encoded = item["photo_data"]
        total += len(encoded) * 3 // 4 - (2 if encoded.endswith("==") else 1 if encoded.endswith("=") else 0)
    if total > MAX_BATCH_BYTES:
        raise PhotoError("本批照片总大小请控制在 40 MB 以内，可减少照片或压缩后上传")
    result = []
    for index, item in enumerate(photos):
        try:
            result.append(local_photo(item["photo_data"], item.get("photo_name", "")))
        except PhotoError as exc:
            raise PhotoError(f"第 {index + 1} 张照片：{exc}") from None
    return result


def public_target(url):
    if not isinstance(url, str) or len(url) > 8192 or any(c.isspace() or ord(c) < 32 for c in url):
        raise PhotoError("请输入完整的 http:// 或 https:// 图片网址，网址中不能有空格")
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError()
        host = parts.hostname.encode("idna").decode("ascii")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port not in {80, 443} or "%" in host:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise PhotoError("仅支持无账号密码的公共 HTTP/HTTPS 图片网址（80/443 端口）") from None
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise PhotoError("图片网站地址无法解析，请检查网址或网络") from None
    ips = []
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        ip = getattr(ip, "ipv4_mapped", None) or ip
        embedded = getattr(ip, "sixtofour", None) or getattr(ip, "teredo", None)
        if not ip.is_global or ip.is_multicast or ip.is_reserved or embedded or ip in ipaddress.ip_network("64:ff9b::/96"):
            raise PhotoError("网址不能指向本机、局域网或保留地址；本机照片请用文件选择框")
        ips.append(str(ip))
    if not ips:
        raise PhotoError("图片网站没有可用的公共地址")
    # 全部地址先通过安全检查，再优先 IPv4；部分本机网络有 IPv6 DNS 但无可用 IPv6 路由。
    return parts, host, port, next((ip for ip in ips if ipaddress.ip_address(ip).version == 4), ips[0])


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address):
        super().__init__(host, port, timeout=20)
        self.address = address

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class PinnedHTTPS(PinnedHTTP):
    def connect(self):
        super().connect()
        # 连接已验证 IP，但仍按原域名验证 TLS 证书，防止 DNS 重绑定。
        self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


def fetch_public(url, accept="image/*", max_bytes=MAX_BYTES):
    """公共网址 GET；验证每个跳转和 IP，不携带账号、Cookie 或云端 Key。"""
    for redirect in range(4):
        parts, host, port, address = public_target(url)
        connection = (PinnedHTTPS if parts.scheme == "https" else PinnedHTTP)(host, port, address)
        try:
            path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parts.query:
                path += "?" + quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
            connection.request("GET", path, headers={"Accept": accept, "Accept-Encoding": "identity",
                                                      "User-Agent": "Tingwen-Video-Workbench/1.5"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or redirect == 3:
                    raise PhotoError("图片网址跳转次数过多或跳转地址无效")
                target = urljoin(url, location)
                if parts.scheme == "https" and urlsplit(target).scheme != "https":
                    raise PhotoError("图片网址跳转到了不安全的 HTTP 地址，请直接填写最终图片网址")
                url = target
                continue
            if response.status != 200:
                raise PhotoError(f"网站拒绝下载（HTTP {response.status}）；可能需要登录或存在防盗链，不会绕过网站限制")
            content_type = response.getheader("Content-Type") or ""
            if accept == "image/*" and "text/html" in content_type.lower():
                raise PhotoError("这看起来是网页地址，请复制图片本身的地址，或先下载照片再选择本机文件")
            size = response.getheader("Content-Length")
            if size and int(size) > max_bytes:
                raise PhotoError(f"网上内容超过 {max_bytes // 1024 // 1024} MB 下载限制")
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise PhotoError(f"网上内容超过 {max_bytes // 1024 // 1024} MB 下载限制")
            encoding = (response.getheader("Content-Encoding") or "identity").strip().lower()
            if encoding in {"gzip", "deflate"}:
                decoder = zlib.decompressobj(31 if encoding == "gzip" else zlib.MAX_WBITS)
                raw = decoder.decompress(raw, max_bytes + 1)
                if len(raw) > max_bytes or decoder.unconsumed_tail:
                    raise PhotoError(f"解压后的内容超过 {max_bytes // 1024 // 1024} MB 下载限制")
                if not decoder.eof:
                    raise PhotoError("网上内容压缩数据不完整，请重新尝试")
            elif encoding not in {"identity", ""}:
                raise PhotoError("网站返回了不支持的压缩格式，请手动复制正文／选择照片")
            return raw, url, content_type
        except PhotoError:
            raise
        except (OSError, http.client.HTTPException, ValueError, UnicodeError, zlib.error):
            raise PhotoError("照片下载失败或超时，请检查网络；也可以下载照片后选择本机文件") from None
        finally:
            connection.close()
    raise PhotoError("图片网址跳转次数过多")


def download_photo(url):
    raw, final_url, _ = fetch_public(url)
    return normalize(raw, "url", urlsplit(final_url).hostname or "")
