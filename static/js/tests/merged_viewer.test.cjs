const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

test('merged viewer translates workspace pages in both directions', () => {
    const messages = [];
    const moves = [];
    const listeners = {};
    const parent = { postMessage: message => messages.push(message) };
    const element = { getContext: () => ({}) };
    const context = vm.createContext({
        window: {
            PDF_VIEWER_CONFIG: {
                embedded: true, chapterId: 10, initialPage: 5,
                mergedChapters: [
                    { chapterId: 10, start: 1, count: 3 },
                    { chapterId: 20, start: 4, count: 5 },
                ],
            },
            parent, location: { origin: 'http://localhost' },
            addEventListener: (name, callback) => { listeners[name] = callback; },
        },
        document: {
            getElementById: () => element,
            querySelector: () => element,
            querySelectorAll: () => [],
        },
        sessionStorage: { getItem: () => null },
        pdfjsLib: { GlobalWorkerOptions: {} },
        goToPage: page => moves.push(page),
    });
    const source = fs.readFileSync(path.join(__dirname, '../manual_pdf_viewer.js'), 'utf8');
    vm.runInContext(source.slice(0, source.indexOf('let scale =')), context);
    assert.equal(messages[0].chapterId, 20);
    assert.equal(messages[0].pageNumber, 2);
    vm.runInContext('pdfDoc = { numPages: 8 };', context);
    const navigate = (chapterId, pageNumber, origin = 'http://localhost') => listeners.message({
        origin, source: parent,
        data: { source: 'manual-chapter-workspace', type: 'go-to-page', chapterId, pageNumber },
    });
    navigate(20, 5);
    navigate(10, 3);
    navigate(20, 6);
    navigate(999, 1);
    navigate(10, 1, 'http://untrusted.example');
    assert.deepEqual(moves, [8, 3]);
});
