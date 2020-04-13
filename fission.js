
const json = require('./xi.json')

const rand = (json) => {
    return json[Math.floor(Math.random() * json.length)]
}

module.exports = async function(context) {
    return {
        status: 200,
        headers: {
            'access-control-allow-headers': 'Content-Type, Content-Length, Authorization, Accept, X-Requested-With',
            'access-control-allow-methods': 'POST, GET',
            'access-control-allow-origin': '*',
            'content-type': 'text/html; charset=utf-8',
        },
        body: rand(json)
    };
}  