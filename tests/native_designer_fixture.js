/* Test-only bootstrap: original Electron main/server/assets, isolated mutable data. */
const fs = require('fs');
const path = require('path');
const http = require('http');
const Module = require('module');
const electron = require('electron');
const args = process.argv.slice(process.argv.findIndex(value => value.endsWith('native_designer_fixture.js')) + 1);
const source = path.resolve(args[0]);
const sandbox = path.resolve(args[1]);
const storagePort = Number(args[2]);
const controlPort = Number(args[3]);
const profile = path.resolve(args[4]);
electron.app.setPath('userData', path.join(sandbox, 'electron-data'));
electron.app.setPath('sessionData', path.join(sandbox, 'electron-session'));
electron.app.commandLine.appendSwitch('disable-gpu');
process.resourcesPath = path.join(sandbox, 'EasySpider', 'resources');
process.chdir(sandbox);
fs.writeFileSync(path.join(sandbox, 'config.json'), JSON.stringify({
  webserver_address:'http://127.0.0.1', webserver_port:storagePort,
  user_data_folder:path.relative(sandbox, profile), debug:false, lang:'zh', copyright:1,
  sys_arch:'x64', mysql_config_path:'./mysql_config.json'
}));
const listen = http.Server.prototype.listen;
http.Server.prototype.listen = function(...args) {
  if (args[0] === 8075) args[0] = 0;
  return listen.apply(this, args);
};
const storageFile = path.join(source, 'server.js');
const storageModule = new Module(storageFile, module);
storageModule.paths = Module._nodeModulePaths(source);
const nativeRequire = Module.createRequire(storageFile);
storageModule.require = name => name === 'electron' ? {
  ...electron, app:new Proxy(electron.app, {get(target, key) {
    if (key === 'isPackaged') return true;
    const value = target[key]; return typeof value === 'function' ? value.bind(target) : value;
  }})
} : nativeRequire(name);
storageModule._compile(fs.readFileSync(storageFile, 'utf8'), storageFile);
require.cache[storageFile] = storageModule;
const selenium = nativeRequire('selenium-webdriver');
const chrome = nativeRequire('selenium-webdriver/chrome');
const setBinary = chrome.Options.prototype.setChromeBinaryPath;
chrome.Options.prototype.setChromeBinaryPath = function(...args) {
  this.addArguments('--headless=new', '--no-sandbox', '--disable-gpu', '--window-size=1400,1100');
  return setBinary.apply(this, args);
};
const buildService = chrome.ServiceBuilder.prototype.build;
chrome.ServiceBuilder.prototype.build = function(...args) {
  const service = buildService.apply(this, args), start = service.start;
  service.start = async function(...startArgs) {
    const address = await start.apply(this, startArgs);
    fs.writeFileSync(path.join(sandbox, 'driver.json'), JSON.stringify({address}));
    return address;
  };
  return service;
};
const buildBrowser = selenium.Builder.prototype.build;
let fixtureDriver;
selenium.Builder.prototype.build = function(...args) {
  fixtureDriver = buildBrowser.apply(this, args); return fixtureDriver;
};
const NativeWindow = electron.BrowserWindow;
function TestWindow(options) {
  const window = new NativeWindow({...options, show:false});
  window.show = () => {};
  return window;
}
Object.setPrototypeOf(TestWindow, NativeWindow);
const mainFile = path.join(source, 'main.js');
const mainModule = new Module(mainFile, module);
mainModule.paths = Module._nodeModulePaths(source);
mainModule.require = name => name === 'electron' ? {...electron, BrowserWindow:TestWindow} : nativeRequire(name);
mainModule._compile(fs.readFileSync(mainFile, 'utf8'), mainFile);
const control = http.createServer(async (req, res) => {
  try {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const input = JSON.parse(Buffer.concat(chunks).toString() || '{}');
    let value;
    if (req.url === '/start') {
      const main = NativeWindow.getAllWindows().find(win => !win.webContents.getURL().includes('/taskGrid/'));
      value = await main.webContents.executeJavaScript(
        `electronAPI.startDesign('zh', ${JSON.stringify(path.relative(sandbox, profile))}, false); true;`);
    } else if (req.url === '/flow') {
      const flow = NativeWindow.getAllWindows().find(win => win.webContents.getURL().includes('/taskGrid/FlowChart'));
      if (!flow) throw new Error('Original flowchart window is not ready');
      value = await flow.webContents.executeJavaScript(input.script);
    } else if (req.url === '/ready') {
      value = NativeWindow.getAllWindows().map(win => win.webContents.getURL());
    } else if (req.url === '/exit') {
      if (fixtureDriver) await fixtureDriver.quit().catch(() => {});
      res.end('{}'); setImmediate(() => electron.app.exit(0)); return;
    } else throw new Error('Unknown fixture operation');
    res.setHeader('Content-Type','application/json'); res.end(JSON.stringify({value}));
  } catch (error) { res.statusCode=500; res.end(JSON.stringify({error:String(error)})); }
});
electron.app.whenReady().then(() => control.listen(controlPort, '127.0.0.1'));
