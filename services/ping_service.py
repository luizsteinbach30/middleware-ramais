import subprocess
import platform
import re


def ping_device(ip):

    param = "-n" if platform.system().lower() == "windows" else "-c"

    cmd = ["ping", param, "1", ip]

    try:
        output = subprocess.check_output(cmd).decode(errors="ignore")

        # 🔥 Suporte a Windows PT-BR e EN
        match = re.search(r'(time|tempo)[=<]\s*(\d+)', output, re.IGNORECASE)

        if match:
            return int(match.group(2))

    except Exception as e:
        print("Ping error:", e)

    return None