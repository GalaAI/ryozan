import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import vm from 'node:vm'
import test from 'node:test'
import ts from 'typescript'

// FounderHubWorkspace tests own agent configuration. This harness exercises the
// shared chat shell's latest-pair display without model calls or customer data.
const require = createRequire(import.meta.url)
const source = await readFile(new URL('./NativeDesktopPortal.tsx', import.meta.url), 'utf8')
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText
const deckSource = await readFile(new URL('./ActionDeck.tsx', import.meta.url), 'utf8')
const deckExports = {}
vm.runInNewContext(ts.transpileModule(deckSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText + '\nexports.deckMagnification = deckMagnification;', {
  exports: deckExports,
  require: name => name === 'gsap' ? { gsap: { registerPlugin() {} } } : name === '@gsap/react' ? {} : require(name),
})
test('dock magnification falls off smoothly for neighbors and stops for touch or reduced motion', () => {
  const weight = deckExports.deckMagnification
  assert.equal(weight(100, 100, false), 1)
  assert.ok(weight(147, 100, false) > 0)
  assert.ok(weight(147, 100, false) < weight(120, 100, false))
  assert.equal(weight(240, 100, false), 0)
  assert.equal(weight(100, null, false), 0)
  assert.equal(weight(100, 100, true), 0)
})
function setup(messages = []) {
  let slot = 0, values
  const state = [{}], exports = {}, sent = []
  const hooks = {
    useState(initial) { const i = slot++; if (!(i in state)) state[i] = initial; return [state[i], value => { state[i] = typeof value === 'function' ? value(state[i]) : value }] },
    useRef: () => ({ current: null }), useMemo: fn => fn(), useCallback: fn => fn, useEffect: () => {},
  }
  vm.runInNewContext(code, { exports, window: { innerWidth:820, innerHeight:1180 }, require: name => {
    if (name === 'react') return hooks
    if (name === 'react/jsx-runtime') return require(name)
    if (name === 'react-dom') return { createPortal: node => node }
    if (name === './template') return { compileTemplate: () => ({ css:'', render: data => { if ('nativeThread' in data) values = data; return null } }) }
    if (name === './source-icons') return { sourceIcons:{}, sourceAgentMarks:{gala:''} }
    if (name === '@bttco/ui') return { ChatBubble:'bubble' }
    return {}
  } })
  const props = { role:'founder',agentId:'gala',activeTab:'pit',actions:[],user:{name:'Test'},unreadCount:0,onSelectTab:()=>{},onSend:text=>sent.push(text),messages }
  return { exports, props, sent, render() { slot=0; exports.NativeDesktopPortal(props); return values } }
}
function nodes(node) { return !node || typeof node !== 'object' ? [] : [node,...[node.props?.children].flat(Infinity).flatMap(nodes)] }
const prompt = id => ({ id, from:'me', text:`Prompt ${id}` })
const reply = id => ({ id, from:'agent', text:`Response ${id}` })

test('groups responses into exchanges without losing orphan or consecutive messages', () => {
  const { exports } = setup()
  const groups = exports.chatExchanges([reply('welcome'),prompt('a'),reply('b'),reply('c'),prompt('d'),prompt('e')])
  assert.equal(groups.length,4)
  assert.equal(groups[0].prompt,undefined)
  assert.equal(groups[1].replies.length,2)
  assert.equal(groups[2].replies.length,0)
})
test('shows the latest pair without an exchange navigation bar', () => {
  const app = setup([prompt('a'),reply('b'),prompt('c'),reply('d')])
  let v=app.render()
  assert.equal(nodes(v.nativeThread).filter(n=>n.type==='bubble').length,2)
  assert.equal(nodes(v.nativeThread).filter(n=>n.type==='nav').length,0)
  app.props.messages.push(prompt('e'),reply('f'))
  v=app.render()
  assert.match(JSON.stringify(v.nativeThread),/Prompt e/)
  assert.doesNotMatch(JSON.stringify(v.nativeThread),/Prompt a|Prompt c/)
})
test('empty input cannot send; Shift Enter and IME do not submit; Enter follows latest', () => {
  const app=setup()
  assert.equal(app.render().stuSend,undefined)
  app.render().stuInputChange({target:{value:'Two lines\nplease'}})
  const key=app.render().stuInputKey
  key({key:'Enter',shiftKey:true,nativeEvent:{},preventDefault(){throw Error('newline prevented')}})
  key({key:'Enter',nativeEvent:{isComposing:true},preventDefault(){throw Error('IME prevented')}})
  assert.equal(app.sent.length,0)
  key({key:'Enter',nativeEvent:{},preventDefault(){}})
  assert.deepEqual(app.sent,['Two lines\nplease'])
  assert.equal(app.render().stuInput,'')
})

test('the composer + only exists where uploads work', () => {
  assert.match(source, /stuPlusToggle:\(\)=>props\.onUpload&&setPlus\(!plus\)/)
  assert.match(source, /stuPlusOpen:plus&&!!props\.onUpload/)
  assert.match(source, /props\.onUpload\?'':'button\[aria-label="Attach or pick talents"\]\{display:none\}'/)
})
