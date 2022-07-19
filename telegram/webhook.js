const bot = require('./index')
if (!process.env.WEBHOOK_PATH) {
    const path = require('path')
    const envpath = path.resolve(__dirname, '.env')
    require('dotenv').config({ path: envpath })
}

const webhook_path = process.env.WEBHOOK_PATH ? process.env.WEBHOOK_PATH : "/secret-path";
const webhook_port = process.env.WEBHOOK_PORT ? process.env.WEBHOOK_PORT : 5000;

console.log(`HTTP webhook started at ${webhook_path} and port ${webhook_port}`)
bot.startWebhook(webhook_path, null, webhook_port)