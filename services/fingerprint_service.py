import requests


def get_device_model(ip):

    try:

        url = f"http://{ip}"

        response = requests.get(
            url,
            timeout=2
        )

        server = response.headers.get("Server", "").lower()

        if "yealink" in server:
            return "Yealink"

        if "fanvil" in server:
            return "Fanvil"

        if "grandstream" in server:
            return "Grandstream"

        if "intelbras" in server:
            return "Intelbras"

        return None

    except:
        return None