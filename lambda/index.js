const Telegraf = require('telegraf')
const rateLimit = require('telegraf-ratelimit')
const json = require('./xi.json')

// Set limit to 1 message per 0.5 seconds
const limitConfig = {
  window: 500,
  limit: 1,
  onLimitExceeded: (ctx, next) => console.log('Rate limit exceeded')
}

const bot = new Telegraf(process.env.BOT_TOKEN)

const random = () => json[Math.floor(Math.random() * json.length)]
const randomBoolean = () => Math.random() >= 0.6

const handler = (ctx) => {
  ctx.telegram.sendMessage(ctx.message.chat.id, random(), {
    reply_to_message_id: ctx.message.message_id
  })
}
const lianRen = (ctx) => {
  if (randomBoolean()) {
    ctx.telegram.sendMessage(ctx.message.chat.id, '怎么？我可是要连任114514年的男人！', {
      reply_to_message_id: ctx.message.message_id
    })
  }
}
const xueXi = (ctx) => {
  if (randomBoolean()) {
    ctx.telegram.sendMessage(ctx.message.chat.id, '好好学！习得者得永生！', {
      reply_to_message_id: ctx.message.message_id
    })
  }
}

bot.start((ctx) => ctx.reply('**习**语Bot，项目地址：https://github.com/neverbehave/xixi-haha'))
bot.command('yiyan', handler)
bot.command('yiyan@xixi_haha_bot', handler)
// Easter EGG
bot.hears(/连任/g, lianRen)
bot.hears(/学习/g, xueXi)

/* AWS Lambda handler function */
exports.handler = (event, context, callback) => {
  const tmp = JSON.parse(event.body); // get data passed to us
  bot.handleUpdate(tmp); // make Telegraf process that data
  return callback(null, { // return something for webhook, so it doesn't try to send same stuff again
    statusCode: 200,
    body: '',
  });
};
