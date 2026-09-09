// No browser or user data: exercise the actual option renderer with a tiny DOM.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../product_evidence_guard/review_assets.py'), 'utf8');
const js = source.split("JS = r'''")[1].split("'''")[0];
new Function(js);
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.value = ''; this.dataset = {}; this.handlers = {}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
}
const elements = new Map();
const document = {
  getElementById: id => { if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id); },
  createElement: tag => new Element(tag), createTextNode: text => text,
};
// Drop startup event registration, but keep the production render functions.
const renderer = js.slice(0, js.indexOf("\ndocument.querySelectorAll('.nav').forEach"));
const context = vm.createContext({document, URLSearchParams, location: {hash: ''}, sessionStorage: {getItem:()=>null},
  window: {}, history: {replaceState(){}}, console, Set, Map});
vm.runInContext(renderer, context);
const c = (id, n, file) => ({candidate_id:id, field:'net_weight', field_label:'净重', normalized_value:n,
  normalized_unit:'g', source_file:file, source_current:true, status:'pending', locator:{line:1}});
context.fixture = {counts:{}, product:{candidates:[c('a',320,'a.txt'),c('b',320,'b.txt'),c('c',300,'c.txt'),c('d',12,'d.txt')],
  facts:[{field:'net_weight',field_label:'净重',fact_status:'conflict',candidate_ids:['a','b','c']},
         {field:'voltage',field_label:'电压',fact_status:'pending_confirmation',candidate_ids:['d']} ]}};
vm.runInContext('state=fixture; renderGroups();', context);
const groups = document.getElementById('groups').children;
const descendants = node => typeof node !== 'object' ? [] : [node,...node.children.flatMap(descendants)];
for (const [index, expected] of [[0,3],[1,1]]) {
  const visible = groups[index].children.filter(n=>n.tag!=='details').flatMap(descendants);
  const links = visible.filter(n=>n.tag==='a');
  assert.equal(links.length, expected);
  assert.ok(links.every(n=>n.href.startsWith('#source=') && n.handlers.click));
}
console.log('Review renderer passed: conflict, pending and each independent source have visible links.');
