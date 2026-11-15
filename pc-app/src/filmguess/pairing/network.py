"""Red local, token de emparejamiento y QR para abrir la web del móvil."""

from __future__ import annotations

import hmac
import io
import ipaddress
import socket

import qrcode
import qrcode.image.svg


def is_allowed_client(host: str | None) -> bool:
    """Indica si una IP de cliente es de la red local (privada, enlace local o el propio PC).

    Args:
        host: IP del cliente tal como la da el servidor.

    Returns:
        ``True`` si se acepta; ``False`` si es pública o no es una IP.
    """
    address = _parse_ip(host)
    if address is None:
        return False
    return address.is_private or address.is_link_local or address.is_loopback


def is_localhost(host: str | None) -> bool:
    """Indica si la petición viene del propio PC.

    Args:
        host: IP del cliente.

    Returns:
        ``True`` para ``127.0.0.0/8`` y ``::1``.
    """
    address = _parse_ip(host)
    return address is not None and address.is_loopback


def token_matches(given: str | None, expected: str) -> bool:
    """Compara el token recibido con el configurado en tiempo constante.

    Args:
        given: Token recibido (``None`` si no llegó).
        expected: Token configurado.

    Returns:
        ``True`` si coinciden y el configurado no está vacío.
    """
    if not given or not expected:
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


def local_ip() -> str:
    """IP del PC en la red local (la de la interfaz con salida por defecto).

    No envía nada: solo pregunta al sistema qué interfaz usaría.

    Returns:
        La IP, o ``127.0.0.1`` si no hay red.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("10.255.255.255", 1))
            return str(sock.getsockname()[0])
        except OSError:
            return "127.0.0.1"


def pairing_url(ip: str, port: int, token: str) -> str:
    """URL que abre la web del móvil ya emparejada.

    El token va en el *fragment* (``#token=``): el navegador no lo envía en las peticiones.

    Args:
        ip: IP del PC.
        port: Puerto del servidor.
        token: Token de emparejamiento.

    Returns:
        La URL.
    """
    return f"http://{ip}:{port}/m/#token={token}"


def qr_text(data: str) -> str:
    """QR en caracteres para la consola.

    Args:
        data: Contenido del QR.

    Returns:
        El QR dibujado con bloques.
    """
    qr = qrcode.QRCode(border=1)
    qr.add_data(data)
    qr.make(fit=True)
    out = io.StringIO()
    qr.print_ascii(out=out, invert=True)
    return out.getvalue()


def qr_svg(data: str) -> bytes:
    """QR en SVG para la tele y el panel.

    Args:
        data: Contenido del QR.

    Returns:
        El documento SVG.
    """
    image = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    out = io.BytesIO()
    image.save(out)
    return out.getvalue()


def _parse_ip(host: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address
