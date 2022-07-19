const bot = require('./index')

module.exports = async function(ctx) {
    // make Telegraf process that data
    await bot.handleUpdate(ctx.request.body, ctx.response).catch(e => console.log(e))
    ctx.response.status(200).end()
};
  