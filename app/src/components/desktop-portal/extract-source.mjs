// Source extraction only: existing DesktopShell has different geometry and the
// supplied DC runtime executes simulation logic. Preserve approved shell bytes
// while giving this native consumer its own data and lifecycle boundary.
import { readFileSync, writeFileSync, cpSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '../../../..')
const source = path.join(root, '.dev/desktop-tablet-frozen-20260830/canon/source')
const html = readFileSync(path.join(source, 'portal-desktop.dc.html'), 'utf8')
const iconsBlock = html.slice(html.indexOf('    const ic = {'),html.indexOf('    /* Nav labels'))
const icons = Object.fromEntries([...iconsBlock.matchAll(/(\w+): I\('([^']+)'\)/g)].map(m=>[m[1],m[2]]))
writeFileSync(path.join(here,'source-icons.ts'), '// Exact SVG paths extracted from approved DC.\nexport const sourceIcons: Record<string,string> = '+JSON.stringify(icons,null,2)+'\n')
const start = html.indexOf('<div ref="{{ pageRef }}"')
const takeover = html.indexOf('<!-- ════ POD TAKEOVER')
const pod = html.indexOf('<div data-screen-label="Pod"', takeover)
const body = html.indexOf('<div style="position:relative;flex:1;min-height:0;overflow-y:auto;padding:20px 20px 22px">', pod)
if ([start, takeover, pod, body].some(n => n < 0)) throw new Error('Approved source markers changed')
const template = html.slice(start, takeover) + html.slice(pod, body)
  + '<div style="position:relative;display:flex;flex-direction:column;flex:1;min-height:0;overflow:hidden">{{ nativeBody }}</div></div></div></div></div>'
writeFileSync(path.join(here, 'shell.html'), template.replace('data-screen-label="Pod"', 'data-screen-label="{{ title }}"').replace('height:100vh;', 'height:{{ compositionHeight }};').replace('<sc-if value="{{ m.hasPicks }}"', '{{ m.cards }}\n<sc-if value="{{ m.hasPicks }}"').split('\n').map(line => line.trimEnd()).join('\n'))
const css = [...html.slice(0, start).matchAll(/<style>([\s\S]*?)<\/style>/g)].map(m => m[1]).join('\n')
// Shadow host is the document boundary. Document-global styles cannot reach mobile.
const baseCSS = ['colors_and_type.css', 'tokens-relic.css', 'patches/dashboard-nexus.css'].map(file => readFileSync(path.join(source,file),'utf8')).join('\n')
writeFileSync(path.join(here, 'source.css'), (baseCSS+'\n'+css).replace(/:root|\bhtml\b|\bbody(?=\s*[{,])/g, ':host').replaceAll("url('fonts/", "url('/desktop-portal/fonts/"))
const motion = html.slice(html.indexOf('  railToggle() {'), html.indexOf('  /* the agent who answers is always'))
  .replaceAll("document.querySelector(", "this.root.querySelector(")
  .replaceAll('setTimeout(', 'this.timeout(').replaceAll('requestAnimationFrame(', 'this.frame(')
  .replaceAll('a.finished.then(f, f)', 'a.finished.then(() => { if (!this.disposed) f(); }, () => { if (!this.disposed) f(); })')
  .replace('const hide = !this.state.railHidden;', "const hide = !this.state.railHidden;\n    if (this.props.reducedMotion || window.matchMedia('(prefers-reduced-motion: reduce)').matches) { this.setState({railHidden:hide,bannerT:hide?'translate(0,-110vh)':'translate(0px,-15px)',railSway:false}); return; }")
writeFileSync(path.join(here, 'rail-motion.js'), `// Mechanically extracted approved motion. Only selectors and timer ownership differ.
export class RailMotion {
  constructor(root, update, reducedMotion) { this.root=root; this.update=update; this.props={reducedMotion}; this.state={}; this.timers=new Set(); this.frames=new Set(); this.disposed=false; }
  setState(patch, callback) { if(this.disposed)return; Object.assign(this.state,patch); this.update(patch); if(callback)this.frame(callback); }
  timeout(fn,ms) { const id=setTimeout(()=>{this.timers.delete(id);if(!this.disposed)fn();},ms);this.timers.add(id);return id; }
  frame(fn) {const id=requestAnimationFrame(()=>{this.frames.delete(id);if(!this.disposed)fn();});this.frames.add(id);return id;}
  dispose(){this.disposed=true;this._introKill?.();this.timers.forEach(clearTimeout);this.frames.forEach(cancelAnimationFrame);this.root.querySelectorAll('*').forEach(el=>el.getAnimations?.().forEach(a=>a.cancel()));}
${motion}
}
`)
const field = readFileSync(path.join(source,'relic-field-gsap.js'),'utf8')
  .replace('(function () {', "import { gsap } from 'gsap';\nimport { CustomEase } from 'gsap/CustomEase';")
  .replace('function start(canvas, host)', 'export function start(canvas, host, reducedMotion = false)')
  .replace('const gsap = window.gsap;', 'const lifetime = new AbortController(); let observer;')
  .replaceAll('window.CustomEase','CustomEase')
  .replace("const reduced = matchMedia", "const reduced = reducedMotion || matchMedia")
  .replace("if (trail.length > 28) trail.shift();\n      });", "if (trail.length > 28) trail.shift();\n      }, { signal: lifetime.signal });")
  .replace("if (trail.length > 28) trail.shift();\r\n      });", "if (trail.length > 28) trail.shift();\r\n      }, { signal: lifetime.signal });")
  .replace('new ResizeObserver(size).observe(host);', 'observer = new ResizeObserver(size); observer.observe(host);')
  .replace("return () => removeEventListener('resize', size);", "return () => { removeEventListener('resize', size); lifetime.abort(); observer?.disconnect(); canvas.__relicField=false; };")
  .replace('tl.kill();', 'tl.kill(); lifetime.abort(); observer?.disconnect();')
  .replace(/\s*window.RelicField = \{ start \};\s*\}\)\(\);\s*$/, '')
writeFileSync(path.join(here,'relic-field.js'), '// Source-derived field with module imports and owned listener cleanup.\n'+field)
const target = path.join(root, 'app/public/desktop-portal')
mkdirSync(target, { recursive: true })
for (const item of ['colors_and_type.css', 'tokens-relic.css', 'patches', 'fonts', 'assets']) {
  cpSync(path.join(source, item), path.join(target, item), { recursive: true })
}
