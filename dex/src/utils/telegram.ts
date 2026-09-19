import { logger } from "@/utils/logger";

const TG_BOT_TOKEN = process.env.TG_BOT_TOKEN;
const TG_CHAT_ID = process.env.TG_CHAT_ID;

export function sendTelegramMessage(text: string): void {
  if (!TG_BOT_TOKEN || !TG_CHAT_ID) return;

  fetch(`https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: TG_CHAT_ID,
      text,
      parse_mode: "HTML",
    }),
  }).catch((err) => {
    logger.warn({ err }, "Failed to send Telegram notification");
  });
}
