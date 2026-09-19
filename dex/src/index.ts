import { Bot } from "@/core/bot";
import { logger } from "@/utils/logger";

process.on("uncaughtException", (err) => {
  logger.error({ err: err.message, stack: err.stack }, "Uncaught exception");
});
process.on("unhandledRejection", (reason) => {
  logger.error({ reason: String(reason) }, "Unhandled rejection");
});

const bot = new Bot();

// Graceful shutdown
process.on("SIGINT", () => {
  logger.info("Shutting down...");
  bot.stop();
  process.exit(0);
});

process.on("SIGTERM", () => {
  logger.info("Shutting down...");
  bot.stop();
  process.exit(0);
});

bot.start().catch((err) => {
  logger.fatal({ err }, "Failed to start bot");
  process.exit(1);
});
