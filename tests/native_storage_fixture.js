/* Runs the unmodified native server in a test-owned data directory. */
const fs = require('fs');
const path = require('path');
const http = require('http');
const Module = require('module');
const source = path.resolve(process.argv[2]);
const sandbox = path.resolve(process.argv[3]);
const port = Number(process.argv[4]);
process.resourcesPath = path.join(sandbox, 'EasySpider', 'resources');
const listen = http.Server.prototype.listen;
http.Server.prototype.listen = function(...args) {
  if (args[0] === 8075) args[0] = 0;
  return listen.apply(this, args);
};
const filename = path.join(source, 'server.js');
const nativeModule = new Module(filename, module);
nativeModule.paths = Module._nodeModulePaths(source);
const nativeRequire = Module.createRequire(filename);
nativeModule.require = name => name === 'electron' ?
  {app:{isPackaged:true, getPath:() => sandbox}, dialog:{}} : nativeRequire(name);
nativeModule._compile(fs.readFileSync(filename, 'utf8'), filename);
nativeModule.exports.start(port);
process.stdin.on('data', () => process.exit(0));
