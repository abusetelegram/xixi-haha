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

const random = (json) => json[Math.floor(Math.random() * json.length)]
const randomBoolean = () => Math.random() >= 0.6
const words = ['怎么？我可是要连任114514年的男人！', '好好学！习得者得永生！'];

const handler = (ctx) => {
  ctx.telegram.sendMessage(ctx.message.chat.id, random(json), {
    reply_to_message_id: ctx.message.message_id
  })
}

const inline_handler = (type, text) => {
  return {
    type: 'article',
    description: text.length <= 30 ? text.substring(0, 30) : text.substring(0, 30) + '...',
    id: Date.now() + Date().milliseconds + type,
    title: type,
    message_text: text
  }
}

const lianRen = (ctx) => {
  if (randomBoolean()) {
    ctx.telegram.sendMessage(ctx.message.chat.id, words[0], {
      reply_to_message_id: ctx.message.message_id
    })
  }
}

const xueXi = (ctx) => {
  if (randomBoolean()) {
    ctx.telegram.sendMessage(ctx.message.chat.id, words[1], {
      reply_to_message_id: ctx.message.message_id
    })
  }
}

bot.start((ctx) => ctx.reply('**习**语Bot，项目地址：https://github.com/neverbehave/xixi-haha'))
bot.command('yiyan', handler)
bot.command('yiyan@xixi_haha_bot', handler)
bot.on('inline_query', (ctx) => {
  ctx.answerInlineQuery([
    inline_handler('就地演讲', random(json)),
    inline_handler('大放厥词', random(words))
  ])
})
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
