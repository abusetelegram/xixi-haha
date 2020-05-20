const bot = require('./index')

module.exports = async function(context) {
    const tmp = context.request.body; // get data passed to us
    bot.handleUpdate(tmp); // make Telegraf process that data
    return {
        status: 200,
        body: "Hello Xixi-haha-bot!"
    }
};
  