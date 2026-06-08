/**
 * batch.js - 批量处理页交互逻辑
 * 内置表格编辑 + Excel/CSV 导入（含亿赛通 COM 读取）
 */

/* ═══════════════════════════════════════════════════════════════════
   TAB SWITCHING
   ═══════════════════════════════════════════════════════════════════ */
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('panel-' + btn.dataset.tab).classList.add('active');
  });
});

/* ═══════════════════════════════════════════════════════════════════
   UTILITY
   ═══════════════════════════════════════════════════════════════════ */
function showToast(message, type = 'info') {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('show'));
  setTimeout(() => { toast.classList.remove('show'); setTimeout(() => toast.remove(), 350); }, 2500);
}

function inferLensType(row) {
  if (row.glass3 && row.T3) return '三胶合';
  if (row.glass2 && row.T2) return '双胶合';
  return '单片';
}

/* ── Input Validation ── */
const NUMERIC_COLS = ['T1','T2','T3','R1','R2','R3','R4','MD1','MD2','MD3','AD1','AD2','AD3','AD4'];

function isValidNumeric(val) {
  if (val === '' || val === '-') return true; // allow empty / typing in progress
  return /^-?\d*\.?\d+$/.test(val);
}

function validateInput(inp) {
  if (!inp.dataset.col || !NUMERIC_COLS.includes(inp.dataset.col)) return true;
  const v = inp.value.trim();
  if (v === '') {
    inp.classList.remove('invalid');
    return true;
  }
  const ok = isValidNumeric(v);
  if (ok) {
    inp.classList.remove('invalid');
  } else {
    inp.classList.add('invalid');
  }
  return ok;
}

/* ═══════════════════════════════════════════════════════════════════
   EDITOR (Built-in table)
   ═══════════════════════════════════════════════════════════════════ */
const editorBody = document.getElementById('editor-body');
const editorInfo = document.getElementById('editor-info');

const COLUMNS = [
  'part_name','part_no','glass1','glass2','glass3',
  'T1','T2','T3','R1','R2','R3','R4',
  'MD1','MD2','MD3','AD1','AD2','AD3','AD4'
];

function createRow(data = {}) {
  const tr = document.createElement('tr');

  COLUMNS.forEach(col => {
    const td = document.createElement('td');
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.value = data[col] !== undefined ? data[col] : '';
    inp.dataset.col = col;
    if (NUMERIC_COLS.includes(col)) {
      inp.classList.add('numeric');
      inp.placeholder = '0';
      inp.addEventListener('blur', () => validateInput(inp));
      inp.addEventListener('input', () => { if (inp.classList.contains('invalid')) validateInput(inp); });
    } else if (['T1','T2','T3','R1','R2','R3','R4','MD1','MD2','MD3','AD1','AD2','AD3','AD4'].includes(col)) {
      inp.placeholder = '0';
    }
    td.appendChild(inp);
    tr.appendChild(td);
  });

  // Type cell (auto inferred from initial data)
  const typeTd = document.createElement('td');
  typeTd.className = 'col-type';
  typeTd.textContent = inferLensType(data);
  tr.appendChild(typeTd);

  // Delete action
  const actTd = document.createElement('td');
  actTd.className = 'col-actions';
  const delBtn = document.createElement('span');
  delBtn.innerHTML = '&times;';
  delBtn.className = 'btn-row';
  delBtn.title = '删除';
  delBtn.addEventListener('click', () => { tr.remove(); updateEditorInfo(); scheduleSave(); });
  actTd.appendChild(delBtn);
  tr.appendChild(actTd);

  // Auto infer type on input
  tr.querySelectorAll('input').forEach(inp => {
    inp.addEventListener('input', () => { typeTd.textContent = inferLensType(getRowData(tr)); });
  });

  return tr;
}

function getRowData(tr) {
  const data = {};
  tr.querySelectorAll('input').forEach(inp => { data[inp.dataset.col] = inp.value; });
  return data;
}

function addRow(data) {
  editorBody.appendChild(createRow(data));
  updateEditorInfo();
}

function updateEditorInfo() {
  const count = editorBody.querySelectorAll('tr').length;
  editorInfo.textContent = `${count} 条记录`;
}

function getAllRows() {
  return Array.from(editorBody.querySelectorAll('tr')).map(getRowData);
}

function clearEditor() {
  editorBody.innerHTML = '';
  updateEditorInfo();
  scheduleSave();
}

// Events
document.getElementById('btn-add-row').addEventListener('click', () => addRow());
document.getElementById('add-row-bar').addEventListener('click', () => addRow());
document.getElementById('btn-clear-rows').addEventListener('click', () => {
  if (!editorBody.querySelectorAll('tr').length) return;
  if (confirm('确定清空所有记录？')) clearEditor();
});

// Export template CSV
document.getElementById('btn-export-template').addEventListener('click', async () => {
  const headers = [
    'PartName','PartNo','Glass1','Glass2','Glass3',
    'T1','T2','T3','R1','R2','R3','R4',
    'MD1','MD2','MD3','AD1','AD2','AD3','AD4'
  ];
  const example = [
    'Lens_01','100.2.001','H-FK61B','H-ZLAF55D','H-ZF11',
    '5.4','1.4','3','35.406','-35.259','147.008','-147.008',
    '12','12','12','12','12','10','10'
  ];
  const csvContent = [headers.join(','), example.join(',')].join('\r\n');

  if (window.pywebview && window.pywebview.api && window.pywebview.api.selectSavePath) {
    try {
      const result = await window.pywebview.api.selectSavePath('lens_batch_template.csv', 'CSV (*.csv)|*.csv');
      const data = JSON.parse(result);
      if (data.success) {
        const res = await fetch('/api/save-text-file', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: data.path, content: csvContent }),
        });
        const saveData = await res.json();
        if (saveData.success) {
          showToast(`模板已保存: ${saveData.path}`, 'success');
        } else {
          showToast('保存失败: ' + saveData.error, 'error');
        }
      } else if (data.error !== 'Cancelled') {
        showToast('选择路径失败: ' + data.error, 'error');
      }
    } catch (e) {
      showToast('导出失败: ' + e.message, 'error');
    }
  } else {
    // Fallback: browser download
    const blob = new Blob(['\uFEFF' + csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'lens_batch_template.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast('模板已下载', 'success');
  }
});

// Editor: restore session on load (or start empty)
// updateEditorInfo() called inside restoreSession or fallback

/* ═══════════════════════════════════════════════════════════════════
   IMPORT (Excel/CSV with COM fallback for 亿赛通)
   ═══════════════════════════════════════════════════════════════════ */
const uploadZone = document.getElementById('upload-zone');
const fileInput = document.getElementById('file-input');
const importToolbar = document.getElementById('import-toolbar');
const importCount = document.getElementById('import-count');
const importTableWrap = document.getElementById('import-table-wrap');
const importBody = document.getElementById('import-body');
const btnImportToEditor = document.getElementById('btn-import-to-editor');

let importedData = [];

function renderImportTable(items) {
  importBody.innerHTML = '';
  items.forEach((item, idx) => {
    const tr = document.createElement('tr');
    const type = item.lens_type || inferLensType(item);
    let badgeClass = 'badge-single';
    if (type === '双胶合') badgeClass = 'badge-doublet';
    if (type === '三胶合') badgeClass = 'badge-triplet';

    tr.innerHTML = `
      <td>${idx + 1}</td>
      <td>${escapeHtml(item.part_name || '')}</td>
      <td>${escapeHtml(item.part_no || '')}</td>
      <td><span class="badge ${badgeClass}">${type}</span></td>
      <td>${escapeHtml(item.glass1 || '')}</td>
      <td>${item.T1 || '-'}</td>
      <td>${item.R1 || '-'}</td>
      <td>${item.R2 || '-'}</td>
      <td>${item.MD1 || '-'}</td>
    `;
    importBody.appendChild(tr);
  });

  importCount.textContent = items.length;
  importToolbar.style.display = 'flex';
  importTableWrap.style.display = 'block';
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

async function handleFile(file) {
  if (!file) return;
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['xlsx','xls','csv'].includes(ext)) {
    showToast('请上传 Excel 或 CSV 文件', 'error');
    return;
  }

  showToast('正在解析文件...');
  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch('/api/batch/parse', { method: 'POST', body: form });
    const data = await res.json();
    if (data.success) {
      importedData = data.data || [];
      renderImportTable(importedData);
      showToast(`成功解析 ${data.count} 条记录`, 'success');
      if (data.warnings && data.warnings.length) {
        console.warn('Import warnings:', data.warnings);
      }
    } else {
      showToast('解析失败: ' + data.error, 'error');
    }
  } catch (err) {
    showToast('上传失败: ' + err.message, 'error');
  }
}

// Upload events
uploadZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', (e) => handleFile(e.target.files[0]));
uploadZone.addEventListener('dragover', (e) => { e.preventDefault(); uploadZone.classList.add('dragover'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
uploadZone.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadZone.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
});

// Import to editor
btnImportToEditor.addEventListener('click', () => {
  if (!importedData.length) { showToast('没有可导入的数据', 'error'); return; }
  importedData.forEach(item => {
    addRow({
      part_name: item.part_name || '',
      part_no: item.part_no || '',
      glass1: item.glass1 || '',
      glass2: item.glass2 || '',
      glass3: item.glass3 || '',
      T1: String(item.T1 || ''),
      T2: String(item.T2 || ''),
      T3: String(item.T3 || ''),
      R1: String(item.R1 || ''),
      R2: String(item.R2 || ''),
      R3: String(item.R3 || ''),
      R4: String(item.R4 || ''),
      MD1: String(item.MD1 || ''),
      MD2: String(item.MD2 || ''),
      MD3: String(item.MD3 || ''),
      AD1: String(item.AD1 || ''),
      AD2: String(item.AD2 || ''),
      AD3: String(item.AD3 || ''),
      AD4: String(item.AD4 || ''),
      save_pdf_folder: item.save_pdf_folder || '',
      mfr_pdf_folder: item.mfr_pdf_folder || '',
    });
  });
  showToast(`已导入 ${importedData.length} 条到编辑器`, 'success');
  // Switch to editor tab
  document.querySelector('[data-tab="editor"]').click();
});

/* ═══════════════════════════════════════════════════════════════════
   BATCH EXPORT (with progress bar)
   ═══════════════════════════════════════════════════════════════════ */

/* ── Progress Overlay Helpers ── */
function showProgress(title, desc) {
  let overlay = document.getElementById('progress-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'progress-overlay';
    overlay.className = 'progress-overlay';
    overlay.innerHTML = `
      <div class="progress-card">
        <h3 id="prog-title">${title}</h3>
        <div class="progress-desc" id="prog-desc">${desc || ''}</div>
        <div class="progress-track"><div class="progress-fill" id="prog-fill"></div></div>
        <div class="progress-stats">
          <span id="prog-counter"></span>
          <span id="prog-status"></span>
        </div>
      </div>`;
    document.body.appendChild(overlay);
  } else {
    document.getElementById('prog-title').textContent = title;
    document.getElementById('prog-desc').textContent = desc || '';
    document.getElementById('prog-fill').style.width = '0%';
    document.getElementById('prog-counter').textContent = '';
    document.getElementById('prog-status').textContent = '';
  }
  requestAnimationFrame(() => overlay.classList.add('active'));
}

function updateProgress(current, total, errors, currentName) {
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;
  const fill = document.getElementById('prog-fill');
  if (fill) fill.style.width = pct + '%';
  const counter = document.getElementById('prog-counter');
  if (counter) counter.textContent = `已完成 ${current} / ${total}`;
  const desc = document.getElementById('prog-desc');
  if (desc && currentName) desc.textContent = '正在导出: ' + currentName;
  const status = document.getElementById('prog-status');
  if (status && errors > 0) {
    status.innerHTML = '<span class="stat-err">失败: ' + errors + '</span>';
  } else if (status) {
    status.innerHTML = '<span class="stat-ok">全部成功</span>';
  }
}

function hideProgress() {
  const overlay = document.getElementById('progress-overlay');
  if (overlay) {
    overlay.classList.remove('active');
    setTimeout(() => { if (overlay.parentNode) overlay.remove(); }, 300);
  }
}

/* ── Export Handler (row-by-row with real progress) ── */
document.getElementById('btn-export-all').addEventListener('click', async () => {
  const rows = getAllRows();
  if (!rows.length) { showToast('没有可导出的记录', 'error'); return; }

  // Validate all rows first
  const allRows = Array.from(editorBody.querySelectorAll('tr'));
  const invalidRows = [];
  allRows.forEach((tr, i) => {
    const inputs = tr.querySelectorAll('input.numeric');
    inputs.forEach(inp => {
      const v = inp.value.trim();
      if (v !== '' && !isValidNumeric(v)) {
        invalidRows.push({ row: i + 1, col: inp.dataset.col, val: v });
        inp.classList.add('invalid');
      }
    });
  });
  if (invalidRows.length > 0) {
    showToast(`第 ${invalidRows[0].row} 行 "${invalidRows[0].col}" 字段包含非法值: ${invalidRows[0].val}`, 'error');
    return;
  }

  // 1. Select output directory via pywebview
  let outputDir = '';
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.selectFolder) {
      const result = await window.pywebview.api.selectFolder();
      const data = JSON.parse(result);
      if (data.success) {
        outputDir = data.path;
      } else {
        showToast('未选择导出目录', 'info');
        return;
      }
    } else {
      showToast('批量导出需要桌面版环境', 'error');
      return;
    }
  } catch (e) {
    showToast('选择目录失败: ' + e.message, 'error');
    return;
  }

  const saveInput = document.getElementById('global-save-folder');
  const mfrInput = document.getElementById('global-mfr-folder');
  const SAVE_RECOMMENDED = 'DTCA110-36';
  const MFR_RECOMMENDED = 'A11036';

  function resolveFolder(inputEl, recommended, fallback) {
    const raw = inputEl ? inputEl.value.trim() : '';
    if (raw === '' || raw === recommended) return fallback;
    return raw;
  }

  const saveFolder = resolveFolder(saveInput, SAVE_RECOMMENDED, 'Save PDF');
  const mfrFolder = resolveFolder(mfrInput, MFR_RECOMMENDED, 'Mfr PDF');

  // 2. Show progress overlay
  showProgress('批量导出中', '正在准备...');
  const total = rows.length;
  let completed = 0;
  let errorCount = 0;
  const allErrors = [];

  // 3. Export rows one at a time
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const rowWithFolder = {
      ...row,
      save_pdf_folder: saveFolder,
      mfr_pdf_folder: mfrFolder,
    };

    const currentName = row.part_name || row.part_no || `行 ${i + 1}`;
    updateProgress(completed, total, errorCount, currentName);

    try {
      const res = await fetch('/api/batch/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rows: [rowWithFolder], output_dir: outputDir }),
      });
      const data = await res.json();
      if (data.success) {
        if (data.errors && data.errors.length > 0) {
          errorCount++;
          allErrors.push(...data.errors.map(e => `[${currentName}] ${e}`));
        }
      } else {
        errorCount++;
        allErrors.push(`[${currentName}] ${data.error}`);
      }
    } catch (err) {
      errorCount++;
      allErrors.push(`[${currentName}] 网络错误: ${err.message}`);
    }

    completed++;
  }

  // 3.5 Export Excel alongside PDFs
  let excelExported = false;
  try {
    const saveFolderName = saveInput.value.trim() || 'batch_data';
    const excelFilename = saveFolderName + '.xlsx';
    const excelRows = rows.map(row => ({
      ...row,
      save_pdf_folder: saveFolder,
      mfr_pdf_folder: mfrFolder,
    }));
    const excelRes = await fetch('/api/batch/export-excel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rows: excelRows, output_dir: outputDir, filename: excelFilename }),
    });
    const excelData = await excelRes.json();
    if (excelData.success) {
      excelExported = true;
    } else {
      console.warn('Excel export failed:', excelData.error);
    }
  } catch (excelErr) {
    console.warn('Excel export error:', excelErr.message);
  }

  // 4. Final progress update
  updateProgress(completed, total, errorCount, '');
  const desc = document.getElementById('prog-desc');
  if (desc) {
    if (errorCount === 0) {
      desc.textContent = `全部 ${total} 条导出成功！`;
    } else {
      desc.textContent = `${total - errorCount} 条成功，${errorCount} 条失败`;
    }
  }

  // 5. Show toast and close progress after delay
  const okCount = total - errorCount;
  if (errorCount === 0) {
    showToast(`全部 ${total} 条导出成功 (Save PDF + Mfr PDF${excelExported ? ' + Excel' : ''})`, 'success');
  } else {
    showToast(`${okCount}/${total} 条成功，${errorCount} 条失败`, 'error');
    if (allErrors.length) {
      console.error('导出错误详情:', allErrors);
      // 在界面中显示错误详情（PyWebview 中用户无法访问浏览器控制台）
      setTimeout(() => {
        alert(`导出完成：${okCount}/${total} 条成功\n\n失败详情：\n` + allErrors.join('\n'));
      }, 500);
    }
  }
  setTimeout(hideProgress, 2000);
});

/* ═══════════════════════════════════════════════════════════════════
   BATCH PASTE (Excel-like multi-cell paste)
   ═══════════════════════════════════════════════════════════════════ */
document.getElementById('editor-table').addEventListener('paste', (e) => {
  const focused = document.activeElement;
  if (!focused || focused.tagName !== 'INPUT' || !focused.dataset.col) return;

  e.preventDefault();
  const clipboardData = e.clipboardData || window.clipboardData;
  const text = clipboardData.getData('text');
  if (!text) return;

  // Split into rows (handle both \r\n and \n)
  const rawLines = text.split(/\r?\n/);
  // Keep trailing empty lines only if they are meaningful (Excel often ends with a trailing newline)
  // We remove completely empty lines at the very end, but keep internal empty lines as intentional blanks
  let lines = rawLines;
  while (lines.length > 0 && lines[lines.length - 1].trim() === '') {
    lines.pop();
  }
  if (!lines.length) return;

  const startTr = focused.closest('tr');
  const allRows = Array.from(editorBody.querySelectorAll('tr'));
  const startRowIndex = allRows.indexOf(startTr);
  const startColIndex = COLUMNS.indexOf(focused.dataset.col);
  if (startRowIndex < 0 || startColIndex < 0) return;

  lines.forEach((line, rIdx) => {
    const targetRowIndex = startRowIndex + rIdx;
    let targetTr;
    if (targetRowIndex < allRows.length) {
      targetTr = allRows[targetRowIndex];
    } else {
      addRow();
      targetTr = editorBody.lastElementChild;
      allRows.push(targetTr);
    }

    const values = line.split('\t');
    values.forEach((val, cIdx) => {
      const targetColIndex = startColIndex + cIdx;
      if (targetColIndex >= COLUMNS.length) return;
      const colName = COLUMNS[targetColIndex];
      const inp = targetTr.querySelector(`input[data-col="${colName}"]`);
      if (inp) {
        inp.value = val.trim();
        validateInput(inp);
        inp.dispatchEvent(new Event('input', { bubbles: true }));
      }
    });
  });

  updateEditorInfo();
  scheduleSave();
});

/* ═══════════════════════════════════════════════════════════════════
   PER-ROW PROCESSING CUSTOMIZATION
   ═══════════════════════════════════════════════════════════════════ */
const rowProcOverrides = new WeakMap();
let activeProcRow = null;

// ── Add "加工" column to createRow ──
const _createRowOrig = createRow;
createRow = function(data) {
  const tr = _createRowOrig(data);

  // 加工按钮（插入到类型列和删除列之间）
  const procTd = document.createElement('td');
  procTd.style.textAlign = 'center';
  const procBtn = document.createElement('span');
  procBtn.className = 'btn-proc';
  procBtn.textContent = '\u2699'; // ⚙
  procBtn.title = '编辑加工要求';
  procBtn.style.cssText = 'cursor:pointer;font-size:14px;color:var(--text-tertiary);padding:2px 6px;border-radius:4px;';
  procBtn.addEventListener('click', () => openDrawIframe(tr));
  procBtn.addEventListener('mouseenter', () => { procBtn.style.background = 'var(--accent-light)'; procBtn.style.color = 'var(--accent)'; });
  procBtn.addEventListener('mouseleave', () => { procBtn.style.background = ''; procBtn.style.color = ''; });
  procTd.appendChild(procBtn);
  // 把加工列插入到倒数第二个位置（删除按钮之前）
  const actionsTd = tr.lastElementChild;
  tr.insertBefore(procTd, actionsTd);

  // 同步自定义标记
  updateProcBtnStyle(tr, procBtn);
  return tr;
};

function updateProcBtnStyle(tr, btn) {
  if (!btn) btn = tr.querySelector('.btn-proc');
  if (!btn) return;
  const overrides = rowProcOverrides.get(tr);
  if (overrides && Object.keys(overrides).length > 0) {
    btn.style.color = '#e67e00';
    btn.style.fontWeight = 'bold';
    btn.title = '加工要求已自定义';
  } else {
    btn.style.color = '';
    btn.style.fontWeight = '';
    btn.title = '编辑加工要求';
  }
}

// ── Modal Logic ──
async function openProcModal(tr) {
  activeProcRow = tr;
  const rowData = getRowData(tr);
  const lensType = inferLensType(rowData);

  // 设置标题
  document.getElementById('proc-modal-title').textContent =
    '编辑加工要求 — ' + (rowData.part_name || '未命名') + ' (' + lensType + ')';

  // 镜片信息
  document.getElementById('proc-modal-lens-info').innerHTML =
    'Glass: ' + [rowData.glass1, rowData.glass2, rowData.glass3].filter(Boolean).join(' / ') +
    ' | T: ' + [rowData.T1, rowData.T2, rowData.T3].filter(Boolean).join(' / ') +
    ' | MD: ' + [rowData.MD1, rowData.MD2, rowData.MD3].filter(Boolean).join(' / ');

  // 只读镜片参数
  const lensRO = document.getElementById('proc-modal-lens-readonly');
  lensRO.innerHTML = '';
  const fields = [
    ['Glass1', 'glass1'], ['Glass2', 'glass2'], ['Glass3', 'glass3'],
    ['T1', 'T1'], ['T2', 'T2'], ['T3', 'T3'],
    ['R1', 'R1'], ['R2', 'R2'], ['R3', 'R3'], ['R4', 'R4'],
    ['MD1', 'MD1'], ['MD2', 'MD2'], ['MD3', 'MD3'],
    ['AD1', 'AD1'], ['AD2', 'AD2'], ['AD3', 'AD3'], ['AD4', 'AD4']
  ];
  const visible = [];
  fields.forEach(([label, key]) => {
    if (rowData[key]) visible.push('<span style="margin-right:12px;white-space:nowrap;font-size:11px;"><b>' + label + '</b>: ' + rowData[key] + '</span>');
  });
  lensRO.innerHTML = visible.join('');

  // 胶合定位镜片行仅胶合时显示，并动态调整可选范围
  const refRow = document.getElementById('proc-modal-ref-row');
  const refSelect = document.getElementById('proc_modal_ref');
  if (refRow) {
    refRow.style.display = (lensType === '双胶合' || lensType === '三胶合') ? 'flex' : 'none';
  }
  if (refSelect && lensType !== '单片') {
    const currentVal = refSelect.value;
    refSelect.innerHTML = '';
    const options = [
      { value: '1', label: '第1片' },
      { value: '2', label: '第2片' },
    ];
    if (lensType === '三胶合') {
      options.push({ value: '3', label: '第3片' });
    }
    options.forEach(opt => {
      const el = document.createElement('option');
      el.value = opt.value;
      el.textContent = opt.label;
      refSelect.appendChild(el);
    });
    // Restore if valid, else default to 2
    if (options.find(o => o.value === currentVal)) {
      refSelect.value = currentVal;
    } else {
      refSelect.value = '2';
    }
  }

  // 加载 settings
  let settings = {};
  try {
    const res = await fetch('/api/settings');
    settings = await res.json();
  } catch(e) { console.warn('加载设置失败'); }

  // 加载已有自定义覆盖
  const existing = rowProcOverrides.get(tr) || {};

  // 填充字段值（优先用已有覆盖，否则用 settings）
  const g = (key, fallback) => { if (existing[key] !== undefined) return existing[key]; if (settings[key] !== undefined) return settings[key]; return fallback; };
  const gs = (id, key, fb) => { const el = document.getElementById(id); if (el) el.value = g(key, fb); };

  gs('proc_modal_c','proc_c_single','60″');
  gs('proc_modal_b','proc_surface_defect','60/40');
  const nMode = g('proc_N_mode','auto');
  setVal('proc_modal_N_mode', nMode);
  gs('proc_modal_N_manual','proc_N_manual','1.5');
  gs('proc_modal_DN','proc_DN','0.3');
  gs('proc_modal_sig','proc_signature','l.y.h');
  setVal('proc_modal_ranking', g('proc_ranking','01'));

  const chMode = g('chamfer_mode','auto');
  setVal('proc_modal_ch_mode', chMode);
  gs('proc_modal_ch_left','chamfer_left','0.2');
  gs('proc_modal_ch_right','chamfer_right','0.4');

  const caMode = g('CA_mode','auto');  // Note: draw module uses CA_mode, settings has ca_ratio only
  setVal('proc_modal_ca_mode', caMode);
  gs('proc_modal_ca_ratio','ca_ratio','0.98');
  gs('proc_modal_CA1','CA1','');
  gs('proc_modal_CA2','CA2','');

  gs('proc_modal_t_tol','t_tol','0.02');
  gs('proc_modal_sag_tol','sag_tol','0.02');
  gs('proc_modal_pos_up','dia_tol_pos_upper','0.010');
  gs('proc_modal_pos_lo','dia_tol_pos_lower','0.025');
  gs('proc_modal_np_up','dia_tol_nonpos_upper','0.05');
  gs('proc_modal_np_lo','dia_tol_nonpos_lower','0.10');
  setVal('proc_modal_ref', g('cemented_ref_lens','2'));

  gs('proc_modal_s1_w1','coat_s1_wave1','420-680');
  gs('proc_modal_s1_w2','coat_s1_wave2','850/940');
  gs('proc_modal_s1_r1','coat_s1_ravg1','0.5');
  gs('proc_modal_s1_r2','coat_s1_ravg2','1');
  gs('proc_modal_s1_a1','coat_s1_angle1','0-22');
  gs('proc_modal_s1_a2','coat_s1_angle2','0-22');
  gs('proc_modal_s2_w1','coat_s2_wave1','420-680');
  gs('proc_modal_s2_w2','coat_s2_wave2','850/940');
  gs('proc_modal_s2_r1','coat_s2_ravg1','0.5');
  gs('proc_modal_s2_r2','coat_s2_ravg2','1');
  gs('proc_modal_s2_a1','coat_s2_angle1','0-22');
  gs('proc_modal_s2_a2','coat_s2_angle2','0-22');

  updateModalConditionals();

  // 显示 modal
  const modal = document.getElementById('proc-modal');
  modal.style.display = 'flex';
  requestAnimationFrame(() => modal.classList.add('active'));
}

function setVal(id, val) {
  const el = document.getElementById(id);
  if (!el) return;
  if (el.tagName === 'SELECT') {
    for (const opt of el.options) { if (opt.value === String(val)) { opt.selected = true; return; } }
  } else {
    el.value = val;
  }
}

function updateModalConditionals() {
  const nMode = document.getElementById('proc_modal_N_mode');
  const nMan = document.getElementById('proc-modal-n-manual');
  if (nMan) nMan.style.display = (nMode && nMode.value === 'manual') ? 'flex' : 'none';

  const chMode = document.getElementById('proc_modal_ch_mode');
  const chL = document.getElementById('proc-modal-ch-left');
  const chR = document.getElementById('proc-modal-ch-right');
  if (chL) chL.style.display = (chMode && chMode.value === 'manual') ? 'flex' : 'none';
  if (chR) chR.style.display = (chMode && chMode.value === 'manual') ? 'flex' : 'none';

  const caMode = document.getElementById('proc_modal_ca_mode');
  const caRatio = document.getElementById('proc-modal-ca-ratio');
  const ca1 = document.getElementById('proc-modal-ca1');
  const ca2 = document.getElementById('proc-modal-ca2');
  if (caRatio) caRatio.style.display = (caMode && caMode.value === 'auto') ? 'flex' : 'none';
  if (ca1) ca1.style.display = (caMode && caMode.value === 'manual') ? 'flex' : 'none';
  if (ca2) ca2.style.display = (caMode && caMode.value === 'manual') ? 'flex' : 'none';
}

// Modal event wiring
['proc_modal_N_mode','proc_modal_ch_mode','proc_modal_ca_mode'].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener('change', updateModalConditionals);
});

document.getElementById('btn-proc-save').addEventListener('click', () => {
  if (!activeProcRow) return;
  const overrides = {};
  const fields = {
    'proc_modal_c': 'proc_c_single', 'proc_modal_b': 'proc_surface_defect',
    'proc_modal_N_manual': 'proc_N_manual', 'proc_modal_DN': 'proc_DN',
    'proc_modal_sig': 'proc_signature',
    'proc_modal_ch_left': 'chamfer_left', 'proc_modal_ch_right': 'chamfer_right',
    'proc_modal_ca_ratio': 'ca_ratio',
    'proc_modal_CA1': 'CA1', 'proc_modal_CA2': 'CA2',
    'proc_modal_t_tol': 't_tol', 'proc_modal_sag_tol': 'sag_tol',
    'proc_modal_pos_up': 'dia_tol_pos_upper', 'proc_modal_pos_lo': 'dia_tol_pos_lower',
    'proc_modal_np_up': 'dia_tol_nonpos_upper', 'proc_modal_np_lo': 'dia_tol_nonpos_lower',
    'proc_modal_s1_w1': 'coat_s1_wave1', 'proc_modal_s1_w2': 'coat_s1_wave2',
    'proc_modal_s1_r1': 'coat_s1_ravg1', 'proc_modal_s1_r2': 'coat_s1_ravg2',
    'proc_modal_s1_a1': 'coat_s1_angle1', 'proc_modal_s1_a2': 'coat_s1_angle2',
    'proc_modal_s2_w1': 'coat_s2_wave1', 'proc_modal_s2_w2': 'coat_s2_wave2',
    'proc_modal_s2_r1': 'coat_s2_ravg1', 'proc_modal_s2_r2': 'coat_s2_ravg2',
    'proc_modal_s2_a1': 'coat_s2_angle1', 'proc_modal_s2_a2': 'coat_s2_angle2',
  };
  const selectFields = {
    'proc_modal_N_mode': 'proc_N_mode', 'proc_modal_ch_mode': 'chamfer_mode',
    'proc_modal_ca_mode': 'CA_mode', 'proc_modal_ranking': 'proc_ranking',
    'proc_modal_ref': 'cemented_ref_lens',
  };

  for (const [elId, key] of Object.entries(fields)) {
    const el = document.getElementById(elId);
    if (el && el.value !== '') overrides[key] = el.value;
  }
  for (const [elId, key] of Object.entries(selectFields)) {
    const el = document.getElementById(elId);
    if (el) overrides[key] = el.value;
  }

  rowProcOverrides.set(activeProcRow, overrides);
  updateProcBtnStyle(activeProcRow);
  closeProcModal();
  showToast('加工参数已保存', 'success');
  scheduleSave();
});

document.getElementById('btn-proc-reset').addEventListener('click', () => {
  if (!activeProcRow) return;
  rowProcOverrides.delete(activeProcRow);
  updateProcBtnStyle(activeProcRow);
  closeProcModal();
  showToast('已恢复为全局默认值', 'info');
  scheduleSave();
});

document.getElementById('btn-proc-cancel').addEventListener('click', closeProcModal);

function closeProcModal() {
  const modal = document.getElementById('proc-modal');
  modal.classList.remove('active');
  setTimeout(() => { modal.style.display = 'none'; }, 250);
  activeProcRow = null;
}
document.getElementById('proc-modal').addEventListener('click', (e) => {
  if (e.target === document.getElementById('proc-modal')) closeProcModal();
});

// ── Update export to include custom_proc ──
const _getAllRowsOrig = getAllRows;
getAllRows = function() {
  const rows = _getAllRowsOrig();
  return rows.map((row, i) => {
    const tr = editorBody.querySelectorAll('tr')[i];
    const overrides = tr ? rowProcOverrides.get(tr) : null;
    if (overrides && Object.keys(overrides).length > 0) {
      row.custom_proc = JSON.stringify(overrides);
    }
    return row;
  });
};

/* ═══════════════════════════════════════════════════════════════════
   PARTNAME AUTO-FILL (from 编码 PDF)
   ═══════════════════════════════════════════════════════════════════ */
function autoFillPartNames() {
  const mfrInput = document.getElementById('global-mfr-folder');
  const code = mfrInput ? mfrInput.value.trim() : '';
  if (!code) return;
  const allTrs = editorBody.querySelectorAll('tr');
  allTrs.forEach((tr, i) => {
    const inp = tr.querySelector('input[data-col="part_name"]');
    if (inp && inp.value === '') {
      inp.value = code + '-' + (i + 1);
      inp.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
}

document.getElementById('global-mfr-folder').addEventListener('blur', () => {
  autoFillPartNames();
  scheduleSave();
});

/* ═══════════════════════════════════════════════════════════════════
   PARTNO AUTO-FILL (from 料号 input)
   ═══════════════════════════════════════════════════════════════════ */
function incrementPartNo(base) {
  const match = base.match(/^(.*?)(\d+)$/);
  if (!match) return base;
  const prefix = match[1];
  const numStr = match[2];
  const num = parseInt(numStr, 10);
  const incremented = num + 1;
  const padded = String(incremented).padStart(numStr.length, '0');
  return prefix + padded;
}

function autoFillPartNos() {
  const baseInput = document.getElementById('global-partno-base');
  const base = baseInput ? baseInput.value.trim() : '';
  if (!base) return;
  const allTrs = editorBody.querySelectorAll('tr');
  let currentNo = base;
  allTrs.forEach(tr => {
    const inp = tr.querySelector('input[data-col="part_no"]');
    if (inp && inp.value === '') {
      inp.value = currentNo;
      inp.dispatchEvent(new Event('input', { bubbles: true }));
    }
    currentNo = incrementPartNo(inp && inp.value ? inp.value : currentNo);
  });
}

function autoFillPartNoForRow(tr, data) {
  if (data && data.part_no) return;
  const baseInput = document.getElementById('global-partno-base');
  const base = baseInput ? baseInput.value.trim() : '';
  if (!base) return;
  const inp = tr.querySelector('input[data-col="part_no"]');
  if (!inp || inp.value !== '') return;
  const allTrs = editorBody.querySelectorAll('tr');
  let lastNo = base;
  for (const prevTr of allTrs) {
    if (prevTr === tr) break;
    const prevInp = prevTr.querySelector('input[data-col="part_no"]');
    if (prevInp && prevInp.value) lastNo = prevInp.value;
  }
  inp.value = incrementPartNo(lastNo);
  inp.dispatchEvent(new Event('input', { bubbles: true }));
}

document.getElementById('global-partno-base').addEventListener('blur', () => {
  autoFillPartNos();
  scheduleSave();
});

/* ═══════════════════════════════════════════════════════════════════
   SESSION PERSISTENCE (sessionStorage)
   ═══════════════════════════════════════════════════════════════════ */
let _saveTimer = null;
function scheduleSave() {
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(saveSession, 500);
}

// Wrap clearEditor to trigger save
const _clearEditorOrig = clearEditor;
clearEditor = function() {
  _clearEditorOrig();
  scheduleSave();
};

function saveSession() {
  try {
    const rows = [];
    const allTrs = editorBody.querySelectorAll('tr');
    allTrs.forEach((tr, i) => {
      const rowData = getRowData(tr);
      const overrides = rowProcOverrides.get(tr);
      if (overrides && Object.keys(overrides).length > 0) {
        rowData._proc_overrides = JSON.parse(JSON.stringify(overrides));
      }
      rows.push(rowData);
    });
    const saveInp = document.getElementById('global-save-folder');
    const mfrInp = document.getElementById('global-mfr-folder');
    const partNoInp = document.getElementById('global-partno-base');
    const toolbar = {
      saveFolder: saveInp ? saveInp.value : '',
      mfrFolder: mfrInp ? mfrInp.value : '',
      partNoBase: partNoInp ? partNoInp.value : '',
    };
    sessionStorage.setItem('batch_session', JSON.stringify({ rows, toolbar }));
  } catch (e) {
    console.warn('Session save failed:', e.message);
  }
}

function restoreSession() {
  try {
    const raw = sessionStorage.getItem('batch_session');
    if (!raw) return false;
    const data = JSON.parse(raw);
    if (!data.rows || data.rows.length === 0) return false;
    data.rows.forEach(rowObj => {
      const procOverrides = rowObj._proc_overrides;
      const cleanData = Object.assign({}, rowObj);
      delete cleanData._proc_overrides;
      const tr = createRow(cleanData);
      editorBody.appendChild(tr);
      if (procOverrides) {
        rowProcOverrides.set(tr, procOverrides);
        updateProcBtnStyle(tr);
      }
    });
    if (data.toolbar) {
      const saveInp = document.getElementById('global-save-folder');
      const mfrInp = document.getElementById('global-mfr-folder');
      const partNoInp = document.getElementById('global-partno-base');
      if (saveInp && data.toolbar.saveFolder) saveInp.value = data.toolbar.saveFolder;
      if (mfrInp && data.toolbar.mfrFolder) mfrInp.value = data.toolbar.mfrFolder;
      if (partNoInp && data.toolbar.partNoBase) partNoInp.value = data.toolbar.partNoBase;
    }
    updateEditorInfo();
    return true;
  } catch (e) {
    console.warn('Session restore failed:', e.message);
    return false;
  }
}

window.addEventListener('beforeunload', () => saveSession());

// ── Wrap addRow: auto-fill PartName/PartNo + scheduleSave ──
const _addRowPrev = addRow;
addRow = function(data) {
  _addRowPrev(data);
  const tr = editorBody.lastElementChild;
  // PartName auto-fill
  if (!data || !data.part_name) {
    const mfrCode = (document.getElementById('global-mfr-folder') || {}).value;
    if (mfrCode && mfrCode.trim()) {
      const inp = tr.querySelector('input[data-col="part_name"]');
      if (inp && inp.value === '') {
        const rowIndex = editorBody.querySelectorAll('tr').length;
        inp.value = mfrCode.trim() + '-' + rowIndex;
        inp.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }
  }
  // PartNo auto-fill
  autoFillPartNoForRow(tr, data);
  scheduleSave();
};

// ── Initialize: restore session or start empty ──
if (!restoreSession()) {
  updateEditorInfo();
}

/* ═══════════════════════════════════════════════════════════════════
   DRAW IFRAME INTEGRATION (replaces proc modal)
   ═══════════════════════════════════════════════════════════════════ */

function openDrawIframe(tr) {
  activeProcRow = tr;
  const rowData = getRowData(tr);
  const lensType = inferLensType(rowData);
  const overrides = rowProcOverrides.get(tr) || {};

  // 设置标题
  const titleEl = document.getElementById('draw-overlay-title');
  if (titleEl) {
    titleEl.textContent = '编辑出图参数 — ' + (rowData.part_name || '未命名') + ' (' + lensType + ')';
  }

  // 显示 overlay
  const overlay = document.getElementById('draw-overlay');
  overlay.style.display = 'flex';

  // 加载 iframe
  const iframe = document.getElementById('draw-iframe');
  iframe.src = '/draw?embed=1';

  // iframe 加载完成后发送数据
  iframe.onload = () => {
    iframe.contentWindow.postMessage({
      type: 'batch-row-data',
      payload: { row: rowData, overrides: overrides }
    }, '*');
  };
}

function closeDrawIframe() {
  const overlay = document.getElementById('draw-overlay');
  if (overlay) overlay.style.display = 'none';
  const iframe = document.getElementById('draw-iframe');
  if (iframe) iframe.src = 'about:blank';
  activeProcRow = null;
}

// 取消按钮
document.getElementById('btn-draw-cancel').addEventListener('click', closeDrawIframe);

// 保存按钮：通知 iframe 执行保存
document.getElementById('btn-draw-save').addEventListener('click', () => {
  const iframe = document.getElementById('draw-iframe');
  if (iframe && iframe.contentWindow) {
    iframe.contentWindow.postMessage({ type: 'draw-request-save' }, '*');
  }
});

// 监听 iframe 保存消息
window.addEventListener('message', (e) => {
  if (!e.data || e.data.type !== 'draw-save') return;
  if (!activeProcRow) return;

  const overrides = e.data.payload;
  rowProcOverrides.set(activeProcRow, overrides);

  // 更新按钮样式
  updateProcBtnStyle(activeProcRow);

  closeDrawIframe();
  showToast('出图参数已保存', 'success');
  scheduleSave();
});
