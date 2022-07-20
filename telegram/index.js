if (!process.env.BOT_TOKEN) {
  const path = require('path')
  const envpath = path.resolve(__dirname, '.env')
  require('dotenv').config({ path: envpath })
}

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
const entries_num = Object.keys(json)
const random = (json) => Math.floor(Math.random() * json.length)
const fart = () => {
  const key = Math.floor(Math.random() * entries_num)
  const art = json[key]
  const word_key = Math.floor(Math.random() * art.text.length)
  const word = art.text[word_key]

  return {
    text: `${word}

::本条来自[${art.title}](http://jhsjk.people.cn/article/${key}), 作者${art.author}, ${art.editor}`,
    key, 
    word_key
  }
}
const randomBoolean = () => Math.random() >= 0.6
const words = ['怎么？我可是要连任114514年的男人！', '好好学！习得者得永生！'];

const handler = (ctx) => {
  ctx.telegram.sendMessage(ctx.message.chat.id, fart().text, {
    reply_to_message_id: ctx.message.message_id, 
    parse_mode: "markdown"
  })
}

const fart_inline_handler = () => {
  const { text, key, word_key } = fart()
  return {
    type: 'article',
    description: text.length <= 30 ? text.substring(0, 30) : text.substring(0, 30) + '...',
    id: `${key}-${word_key}`,
    title: "就地演讲",
    message_text: text
  }
}

const random_inline_handler = () => {
  const text =  random(words)
  const type = '大放厥词'
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
    random_inline_handler(),
    fart_inline_handler()
  ])
})
// Easter EGG
bot.hears(/连任/g, lianRen)
bot.hears(/学习/g, xueXi)

module.exports = bot