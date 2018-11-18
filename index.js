// content of index.js
const http = require('http')
const port = 3000

const json = require('./xi.json')

const requestHandler = (request, response) => {
  response.writeHead(200,{
	  'access-control-allow-headers': 'Content-Type, Content-Length, Authorization, Accept, X-Requested-With',
	  'access-control-allow-methods': 'POST, GET',
	  'access-control-allow-origin': '*',
	  'content-type': 'text/html; charset=utf-8',
  })
  response.end(json[Math.floor(Math.random() * json.length)])
}

const server = http.createServer(requestHandler)

server.listen(port, (err) => {
  if (err) {
    return console.log('something bad happened', err)
  }

  console.log(`server is listening on ${port}`)
})
