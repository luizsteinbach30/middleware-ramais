import subprocess
import re


def get_mac_address(ip):

    try:
        output = subprocess.check_output(["arp", "-a"]).decode(errors="ignore")

        # Padrão Windows
        pattern = re.compile(rf"{ip}\s+([a-f0-9\-]{{17}})", re.IGNORECASE)

        match = pattern.search(output)

        if match:
            return match.group(1)

    except Exception as e:
        print("ARP error:", e)

    return None