const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'js', 'draw.js'),
  'utf8',
);

const elements = new Map();
const parentMessages = [];
const parentWindow = {
  postMessage: (message, targetOrigin) => parentMessages.push({ message, targetOrigin }),
};
const sandbox = {
  URLSearchParams,
  console,
  setTimeout: () => 0,
  clearTimeout: () => {},
  requestAnimationFrame: callback => callback(),
  window: {
    location: { search: '', origin: 'http://127.0.0.1:5000' },
    addEventListener: () => {},
    parent: parentWindow,
  },
  document: {
    getElementById: id => elements.get(id) || null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
  },
};
vm.createContext(sandbox);
vm.runInContext(`${source}\n;globalThis.__apertureTestApi = {
  model: getApertureModel,
  entries: apertureEntries,
  applyOverrides,
  saveAndReturn,
  setSapphireSurfaces: values => { sapphireSurfaces = values; },
  clampPreviewIndex,
  distanceToRectSquared,
  setDoublet: value => { apertureStates.doublet = value; },
  setTriplet: value => { apertureStates.triplet = value; },
};`, sandbox);

const api = sandbox.__apertureTestApi;

function state(split1, split2) {
  return {
    outerLeft: '30',
    interface1Left: '22',
    interface1Right: '20',
    interface2Left: '18',
    interface2Right: '16',
    outerRight: '14',
    split1,
    split2,
  };
}

const expected = [
  [false, false, [30, 22, 18, 14], [[30, 22], [22, 18], [18, 14]]],
  [true, false, [30, 22, 20, 18, 14], [[30, 22], [20, 18], [18, 14]]],
  [false, true, [30, 22, 18, 16, 14], [[30, 22], [22, 18], [16, 14]]],
  [true, true, [30, 22, 20, 18, 16, 14], [[30, 22], [20, 18], [16, 14]]],
];

for (const [split1, split2, sequence, lenses] of expected) {
  api.setTriplet(state(split1, split2));
  const model = api.model('triplet');
  assert.deepStrictEqual(Array.from(model.sequence), sequence);
  assert.deepStrictEqual(
    Array.from(model.lenses, lens => [lens.left, lens.right]),
    lenses,
  );
  assert.strictEqual(api.entries('triplet').length, sequence.length);
}

api.setDoublet({
  outerLeft: '30',
  interface1Left: '22',
  interface1Right: '20',
  outerRight: '14',
  split1: true,
});
assert.deepStrictEqual(Array.from(api.model('doublet').sequence), [30, 22, 20, 14]);
assert.deepStrictEqual(
  Array.from(api.model('doublet').lenses, lens => [lens.left, lens.right]),
  [[30, 22], [20, 14]],
);

elements.set('N_mode', { value: 'auto' });
elements.set('N_manual', { value: '' });
elements.set('proc_vendor', { value: '' });
elements.set('proc_ranking', { value: '' });
elements.set('proc_molding', { value: '' });
api.applyOverrides({
  N_mode: 'manual',
  N_manual: '1.5',
  proc_vendor: 'LEGACY-VENDOR',
  proc_ranking: 'CUSTOM-GRADE',
  proc_molding: 'CUSTOM-MOLD',
});
assert.strictEqual(elements.get('N_mode').value, 'manual');
assert.strictEqual(elements.get('N_manual').value, '1.5');
assert.strictEqual(elements.get('proc_vendor').value, 'LEGACY-VENDOR');
assert.strictEqual(elements.get('proc_ranking').value, 'CUSTOM-GRADE');
assert.strictEqual(elements.get('proc_molding').value, 'CUSTOM-MOLD');

api.setSapphireSurfaces(['1:S2', '2:S1']);

api.saveAndReturn();
const saved = parentMessages.at(-1);
assert.strictEqual(saved.targetOrigin, 'http://127.0.0.1:5000');
assert.strictEqual(saved.message.type, 'draw-save');
assert.strictEqual(saved.message.payload.proc_N_mode, 'manual');
assert.strictEqual(saved.message.payload.proc_N_manual, '1.5');
assert.strictEqual(saved.message.payload.proc_vendor, 'LEGACY-VENDOR');
assert.strictEqual(saved.message.payload.proc_ranking, 'CUSTOM-GRADE');
assert.strictEqual(saved.message.payload.proc_molding, 'CUSTOM-MOLD');
assert.deepStrictEqual(
  Array.from(saved.message.payload.sapphire_surfaces),
  ['1:S2', '2:S1'],
);
assert.ok(!Object.hasOwn(saved.message.payload, 'N_mode'));
assert.ok(!Object.hasOwn(saved.message.payload, 'N_manual'));

assert.strictEqual(api.clampPreviewIndex(2, 3), 2);
assert.strictEqual(api.clampPreviewIndex(2, 1), 0);
assert.strictEqual(api.clampPreviewIndex(-1, 3), 0);
assert.strictEqual(api.distanceToRectSquared(5, 5, {left:0, top:0, right:10, bottom:10}), 0);
assert.strictEqual(api.distanceToRectSquared(13, 14, {left:0, top:0, right:10, bottom:10}), 25);

console.log('aperture model split combinations: ok');
