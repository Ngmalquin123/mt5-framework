"""python-telegram-bot simulado: los tests nunca envían mensajes reales."""

SENT_MESSAGES: list = []


class Bot():

    def __init__(self, token: str):
        if not token:
            raise ValueError("Token vacío")
        self.token = token

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def send_message(self, text: str, chat_id: str):
        if self.token == "fail":
            raise RuntimeError("Telegram no disponible")
        SENT_MESSAGES.append((chat_id, text))
