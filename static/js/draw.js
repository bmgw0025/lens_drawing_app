/**
 * draw.js - 自定义出图页交互逻辑
 * 参数收集 → API 调用 → 预览刷新 → PDF 导出
 */

/* ── State ── */
let previewTimer = null;
let isExporting = false;
let previewImages = [];    // 多图预览缓存 [{label, b64, fields, img_w, img_h}, ...]
let previewIndex = 0;     // 当前显示的图片索引
let currentImageBackendSize = null;  // {w, h} 后端返回的实际像素尺寸，用于精确 overlay 定位

/* ── Embedded mode detection ── */
const _urlParams = new URLSearchParams(window.location.search);
const embeddedMode = _urlParams.get('embed') === '1';

/* ── DOM refs ── */
const els = {
  previewImg: document.getElementById('preview-img'),
  previewLoading: document.getElementById('preview-loading'),
  previewError: document.getElementById('preview-error'),
  previewStatus: document.getElementById('preview-status'),

  btnExport: document.getElementById('btn-export'),
  btnReset: document.getElementById('btn-reset'),

  exportModal: document.getElementById('export-modal'),
  exportFilename: document.getElementById('export-filename'),
  exportPath: document.getElementById('export-path'),
  btnChoosePath: document.getElementById('btn-choose-path'),
  btnCancelExport: document.getElementById('btn-cancel-export'),
  btnConfirmExport: document.getElementById('btn-confirm-export'),
};

/* ── Utility ── */
function showToast(message, type = 'info') {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);

  requestAnimationFrame(() => toast.classList.add('show'));
  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 350);
  }, 2500);
}

function setPreviewState(state, data = null) {
  els.previewLoading.style.display = 'none';
  els.previewImg.style.display = 'none';
  els.previewError.style.display = 'none';
  clearPreviewOverlays();
  const tabs = document.getElementById('page-tabs');
  if (tabs) tabs.style.display = 'none';

  if (state === 'loading') {
    els.previewLoading.style.display = 'block';
    els.previewStatus.textContent = '正在生成预览...';
  } else if (state === 'success') {
    els.previewImg.src = 'data:image/png;base64,' + data;
    els.previewImg.style.display = 'block';
    els.previewStatus.textContent = '预览已更新';
  } else if (state === 'error') {
    els.previewError.textContent = data || '生成预览失败';
    els.previewError.style.display = 'block';
    els.previewStatus.textContent = '预览失败';
  }
}

/* ── Multi-image preview (cemented) ── */
function showPreviewImage(index) {
  if (index < 0 || index >= previewImages.length) return;
  previewIndex = index;
  const img = previewImages[index];
  els.previewImg.src = 'data:image/png;base64,' + img.b64;
  els.previewImg.style.display = 'block';
  els.previewStatus.textContent = img.label;
  // 记录后端返回的精确尺寸
  currentImageBackendSize = (img.img_w && img.img_h) ? {w: img.img_w, h: img.img_h} : null;
  updatePageTabs();
  // 渲染该页的字段覆盖层
  if (img.fields && img.fields.length > 0) {
    renderPreviewOverlays(img.fields);
  } else {
    clearPreviewOverlays();
  }
}

function switchPreviewPage(delta) {
  if (previewImages.length < 2) return;
  const newIdx = previewIndex + delta;
  if (newIdx >= 0 && newIdx < previewImages.length) {
    showPreviewImage(newIdx);
  }
}

function updatePageTabs() {
  const tabs = document.getElementById('page-tabs');
  if (!tabs) return;
  tabs.innerHTML = '';
  if (previewImages.length < 2) return;
  tabs.style.display = 'flex';
  previewImages.forEach((img, i) => {
    const btn = document.createElement('span');
    btn.className = 'page-tab' + (i === previewIndex ? ' active' : '');
    btn.textContent = img.label;
    btn.addEventListener('click', () => showPreviewImage(i));
    tabs.appendChild(btn);
  });
}

function setPreviewMulti(images, labels, fieldsByPage, imageSizes) {
  els.previewLoading.style.display = 'none';
  els.previewError.style.display = 'none';
  previewImages = images.map((b64, i) => ({
    label: labels[i],
    b64,
    fields: (fieldsByPage && fieldsByPage[i]) ? fieldsByPage[i] : [],
    img_w: (imageSizes && imageSizes[i]) ? imageSizes[i].w : null,
    img_h: (imageSizes && imageSizes[i]) ? imageSizes[i].h : null,
  }));
  previewIndex = 0;
  if (previewImages.length > 0) {
    showPreviewImage(0);
  }
  els.previewStatus.textContent = '预览已更新（' + previewImages.length + ' 页）';
}

/* ── Preview overlays (editable fields on top of image) ── */
let currentFields = [];

function alignOverlayContainer() {
  const img = els.previewImg;
  const container = document.getElementById('preview-overlays');
  const parent = document.getElementById('preview-container');
  if (!img || !container || !parent || img.style.display === 'none') return;
  if (!img.naturalWidth || !img.naturalHeight) return;

  // Use clientWidth/clientHeight to exclude border from calculation
  const parentW = parent.clientWidth;
  const parentH = parent.clientHeight;
  // Prefer backend-reported size to eliminate browser decoder rounding discrepancies
  const imgW = (currentImageBackendSize && currentImageBackendSize.w) || img.naturalWidth;
  const imgH = (currentImageBackendSize && currentImageBackendSize.h) || img.naturalHeight;
  const imgAspect = imgW / imgH;
  const parentAspect = parentW / parentH;

  // Calculate actual image display area (object-fit: contain)
  let displayW, displayH, offsetX, offsetY;
  if (imgAspect > parentAspect) {
    displayW = parentW;
    displayH = parentW / imgAspect;
    offsetX = 0;
    offsetY = (parentH - displayH) / 2;
  } else {
    displayH = parentH;
    displayW = parentH * imgAspect;
    offsetX = (parentW - displayW) / 2;
    offsetY = 0;
  }

  // Position overlay to exactly match the image content area
  container.style.left = offsetX + 'px';
  container.style.top = offsetY + 'px';
  container.style.width = displayW + 'px';
  container.style.height = displayH + 'px';
}

function renderPreviewOverlays(fields) {
  const container = document.getElementById('preview-overlays');
  if (!container) return;
  container.innerHTML = '';
  currentFields = fields || [];
  if (!fields || fields.length === 0) return;

  // Align immediately if image is loaded, otherwise wait
  if (els.previewImg.complete && els.previewImg.naturalWidth > 0) {
    alignOverlayContainer();
  }
  els.previewImg.addEventListener('load', alignOverlayContainer, { once: true });

  fields.forEach(f => {
    const div = document.createElement('div');
    div.className = 'preview-field';
    div.style.left = f.left_pct + '%';
    div.style.top = f.top_pct + '%';
    div.style.width = f.w_pct + '%';
    div.style.height = f.h_pct + '%';
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.value = f.value || '';
    inp.title = f.label + (f.source === 'calc' ? '（自动计算，编辑则切为手动）' : '');
    inp.dataset.fieldId = f.id;
    inp.dataset.source = f.source;
    inp.addEventListener('change', onFieldEdit);
    inp.addEventListener('blur', onFieldEdit);
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { inp.blur(); }
    });
    div.appendChild(inp);
    container.appendChild(div);
  });
}

function onFieldEdit(e) {
  const inp = e.target;
  const fieldId = inp.dataset.fieldId;
  const source = inp.dataset.source;
  const newVal = inp.value.trim();
  if (!newVal) return;

  const calcFields = {
    'chamfer': () => { setParam('chamfer_mode', 'manual'); setParam('chamfer_left', newVal); },
    'ca1': () => { setParam('CA_mode', 'manual'); setParam('CA1', newVal); },
    'ca2': () => { setParam('CA_mode', 'manual'); setParam('CA2', newVal); },
    'n_val': () => { setParam('N_mode', 'manual'); setParam('N_manual', newVal); },
  };
  const directFields = {
    'vendor': () => {},
    'ranking': () => { setParam('proc_ranking', newVal); },
    'c_val': () => { setParam('proc_c_single', newVal); },
    'dn_val': () => { setParam('DN', newVal); },
    'b_val': () => { setParam('proc_b', newVal); },
    'signature': () => { setParam('signature', newVal); },
  };

  if (source === 'calc' && calcFields[fieldId]) {
    calcFields[fieldId]();
  } else if (directFields[fieldId]) {
    directFields[fieldId]();
  }
  refreshPreview();
}

function setParam(id, val) {
  const el = document.getElementById(id);
  if (el) { el.value = val; el.dispatchEvent(new Event('input', { bubbles: true })); }
}

function clearPreviewOverlays() {
  const container = document.getElementById('preview-overlays');
  if (container) container.innerHTML = '';
  currentFields = [];
}

/* ── Lens type helpers ── */
function getLensType() {
  const el = document.querySelector('input[name="lens_type"]:checked');
  return el ? el.value : 'single';
}

function buildLenses(params) {
  const lenses = [{
    glass: params.glass_name,
    T: params.T,
    R_left: params.R1,
    R_right: params.R2,
    MD: params.MD,
    AD_left: params.AD1,
    AD_right: params.AD2,
  }];
  if (params.lens_type !== 'single') {
    lenses.push({
      glass: params.glass2,
      T: params.T2,
      R_left: params.R2,
      R_right: params.R3,
      MD: params.MD2,
      AD_left: params.AD2,
      AD_right: params.AD3,
    });
  }
  if (params.lens_type === 'triplet') {
    lenses.push({
      glass: params.glass3,
      T: params.T3,
      R_left: params.R3,
      R_right: params.R4,
      MD: params.MD3,
      AD_left: params.AD3,
      AD_right: params.AD4,
    });
  }
  return lenses;
}

function updateLensTypeUI() {
  const type = getLensType();
  const l2Fields = document.querySelectorAll('.lens2-field');
  const l3Fields = document.querySelectorAll('.lens3-field');

  l2Fields.forEach(el => {
    if (type === 'single') {
      el.classList.remove('visible');
    } else {
      el.classList.add('visible');
    }
  });

  l3Fields.forEach(el => {
    if (type === 'triplet') {
      el.classList.add('visible');
    } else {
      el.classList.remove('visible');
    }
  });

  // Cemented reference lens row: visible only for doublet/triplet
  const refRow = document.getElementById('cemented-ref-row');
  const refSelect = document.getElementById('cemented_ref_lens');
  if (refRow) {
    refRow.style.display = (type === 'single') ? 'none' : 'flex';
  }
  // Dynamically rebuild options: doublet → 1/2, triplet → 1/2/3
  if (refSelect && type !== 'single') {
    const currentVal = refSelect.value;
    refSelect.innerHTML = '';
    const options = [
      { value: '1', label: '第 1 片' },
      { value: '2', label: '第 2 片' },
    ];
    if (type === 'triplet') {
      options.push({ value: '3', label: '第 3 片' });
    }
    options.forEach(opt => {
      const el = document.createElement('option');
      el.value = opt.value;
      el.textContent = opt.label;
      refSelect.appendChild(el);
    });
    // Restore if still valid, else default to 2
    if (options.find(o => o.value === currentVal)) {
      refSelect.value = currentVal;
    } else {
      refSelect.value = '2';
    }
  }
}

/* ── Collect params from inputs ── */
function collectParams() {
  const getVal = (id) => {
    const el = document.getElementById(id);
    return el ? el.value : '';
  };
  const getNum = (id) => parseFloat(getVal(id)) || 0;
  const getCheck = (id) => {
    const el = document.getElementById(id);
    return el ? el.checked : false;
  };

  return {
    lens_type: getLensType(),

    T: getNum('T'),
    R1: getNum('R1'),
    R2: getNum('R2'),
    MD: getNum('MD'),
    AD1: getNum('AD1'),
    AD2: getNum('AD2'),
    CA1: getVal('CA1'),
    CA2: getVal('CA2'),
    CA_mode: getVal('CA_mode'),
    ca_ratio: getNum('ca_ratio'),

    part_name: getVal('part_name'),
    part_no: getVal('part_no'),
    glass_name: getVal('glass_name'),

    glass2: getVal('glass2'),
    T2: getNum('T2'),
    R3: getNum('R3'),
    MD2: getNum('MD2'),
    AD3: getNum('AD3'),

    glass3: getVal('glass3'),
    T3: getNum('T3'),
    R4: getNum('R4'),
    MD3: getNum('MD3'),
    AD4: getNum('AD4'),

    coat_preset: getVal('coat_preset'),

    coat_s1_wave1: getVal('coat_s1_wave1'),
    coat_s1_wave2: getVal('coat_s1_wave2'),
    coat_s2_wave1: getVal('coat_s2_wave1'),
    coat_s2_wave2: getVal('coat_s2_wave2'),

    coat_s1_ravg1: getVal('coat_s1_ravg1'),
    coat_s1_ravg2: getVal('coat_s1_ravg2'),
    coat_s2_ravg1: getVal('coat_s2_ravg1'),
    coat_s2_ravg2: getVal('coat_s2_ravg2'),

    coat_s1_angle1: getVal('coat_s1_angle1'),
    coat_s1_angle2: getVal('coat_s1_angle2'),
    coat_s2_angle1: getVal('coat_s2_angle1'),
    coat_s2_angle2: getVal('coat_s2_angle2'),

    proc_c_single: getVal('proc_c_single'),
    proc_c_assembly: getVal('proc_c_assembly'),
    proc_b: getVal('proc_b'),
    proc_ranking: getVal('proc_ranking'),
    N_mode: getVal('N_mode'),
    N_manual: getVal('N_manual'),
    DN: getVal('DN'),
    signature: getVal('signature'),

    chamfer_mode: getVal('chamfer_mode'),
    chamfer_left: getNum('chamfer_left'),
    chamfer_right: getNum('chamfer_right'),

    t_tol: getNum('t_tol'),
    sag_tol: getNum('sag_tol'),
    dia_tol_pos_upper: getNum('dia_tol_pos_upper'),
    dia_tol_pos_lower: getNum('dia_tol_pos_lower'),
    dia_tol_nonpos_upper: getNum('dia_tol_nonpos_upper'),
    dia_tol_nonpos_lower: getNum('dia_tol_nonpos_lower'),
    cemented_ref_lens: parseInt(getVal('cemented_ref_lens')) || 2,
  };
}

/* ── Preview ── */
async function refreshPreview() {
  if (previewTimer) clearTimeout(previewTimer);

  previewTimer = setTimeout(async () => {
    setPreviewState('loading');
    const params = collectParams();

    try {
      let res, data;
      if (params.lens_type === 'single') {
        res = await fetch('/api/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params),
        });
      } else {
        res = await fetch('/api/preview/cemented', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            lenses: buildLenses(params),
            part_name: params.part_name,
            part_no: params.part_no,
            // Proc overrides for cemented assembly page
            proc_c_single: params.proc_c_single,
            proc_c_assembly: params.proc_c_assembly,
            proc_b: params.proc_b,
            proc_ranking: params.proc_ranking,
            N_mode: params.N_mode,
            N_manual: params.N_manual,
            DN: params.DN,
            signature: params.signature,
            chamfer_mode: params.chamfer_mode,
            chamfer_left: params.chamfer_left,
            chamfer_right: params.chamfer_right,
            t_tol: params.t_tol,
            sag_tol: params.sag_tol,
            dia_tol_pos_upper: params.dia_tol_pos_upper,
            dia_tol_pos_lower: params.dia_tol_pos_lower,
            dia_tol_nonpos_upper: params.dia_tol_nonpos_upper,
            dia_tol_nonpos_lower: params.dia_tol_nonpos_lower,
            cemented_ref_lens: params.cemented_ref_lens,
          }),
        });
      }
      data = await res.json();

          if (data.success) {
        if (data.images && data.labels) {
          // Multi-image (cemented): assembly + individual pages
          setPreviewMulti(data.images, data.labels, data.fields_by_page, data.image_sizes);
        } else if (data.image) {
          // Single image (single lens)
          previewImages = [];
          currentImageBackendSize = (data.img_w && data.img_h) ? {w: data.img_w, h: data.img_h} : null;
          setPreviewState('success', data.image);
          if (data.fields) renderPreviewOverlays(data.fields);
          else clearPreviewOverlays();
        }
      } else {
        setPreviewState('error', data.error);
      }
    } catch (err) {
      setPreviewState('error', '网络请求失败: ' + err.message);
    }
  }, 350);
}

/* ── Export PDF ── */
async function doExport(fullpath) {
  if (isExporting) return;
  isExporting = true;
  els.btnConfirmExport.textContent = '导出中...';

  const params = collectParams();

  try {
    let res, data;
    if (params.lens_type === 'single') {
      params.filepath = fullpath;
      res = await fetch('/api/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      });
    } else {
      res = await fetch('/api/export/cemented', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lenses: buildLenses(params),
          part_name: params.part_name,
          part_no: params.part_no,
          filepath: fullpath,
          // Proc overrides for cemented assembly page
          proc_c_single: params.proc_c_single,
          proc_c_assembly: params.proc_c_assembly,
          proc_b: params.proc_b,
          proc_ranking: params.proc_ranking,
          N_mode: params.N_mode,
          N_manual: params.N_manual,
          DN: params.DN,
          signature: params.signature,
          chamfer_mode: params.chamfer_mode,
          chamfer_left: params.chamfer_left,
          chamfer_right: params.chamfer_right,
          t_tol: params.t_tol,
          sag_tol: params.sag_tol,
          dia_tol_pos_upper: params.dia_tol_pos_upper,
          dia_tol_pos_lower: params.dia_tol_pos_lower,
          dia_tol_nonpos_upper: params.dia_tol_nonpos_upper,
          dia_tol_nonpos_lower: params.dia_tol_nonpos_lower,
          cemented_ref_lens: params.cemented_ref_lens,
        }),
      });
    }
    data = await res.json();

    if (data.success) {
      showToast(`PDF 已保存: ${data.path}`, 'success');
      closeExportModal();
    } else {
      showToast('导出失败: ' + data.error, 'error');
    }
  } catch (err) {
    showToast('导出失败: ' + err.message, 'error');
  } finally {
    isExporting = false;
    els.btnConfirmExport.textContent = '导出';
  }
}

/* ── Modal ── */
function openExportModal() {
  const params = collectParams();
  const suggested = `lens_T${params.T}_R1${params.R1}_R2${params.R2}.pdf`;
  els.exportFilename.value = suggested;
  if (els.exportPath) els.exportPath.value = '';
  els.exportModal.style.display = 'flex';
  requestAnimationFrame(() => els.exportModal.classList.add('active'));
  els.exportFilename.focus();
}

function closeExportModal() {
  els.exportModal.classList.remove('active');
  setTimeout(() => { els.exportModal.style.display = 'none'; }, 250);
}

async function chooseSavePath() {
  const filename = els.exportFilename.value.trim() || 'lens_drawing.pdf';
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.selectSavePath) {
      const result = await window.pywebview.api.selectSavePath(filename);
      const data = JSON.parse(result);
      if (data.success) {
        els.exportPath.value = data.path;
      } else if (data.error !== 'Cancelled') {
        showToast('选择路径失败: ' + data.error, 'error');
      }
    } else {
      showToast('路径选择仅在桌面版可用，请在文件名中输入完整路径', 'info');
    }
  } catch (e) {
    showToast('路径选择失败: ' + e.message, 'error');
  }
}

/* ── Reset ── */
function resetParams() {
  const defaults = {
    T: '5.4', R1: '35.406', R2: '-35.259', MD: '13.5',
    AD1: '13.5', AD2: '13.5',
    CA1: '13.00', CA2: '13.00',
    CA_mode: 'auto', ca_ratio: '0.98',
    part_name: 'singlelen', part_no: '100.2.00888', glass_name: 'H-FK61B',
    glass2: 'H-ZLAF55D', T2: '1.4', R3: '147.008', MD2: '13.5', AD3: '12',
    glass3: 'H-ZF11', T3: '3', R4: '-147.008', MD3: '12', AD4: '12',
    coat_preset: 'SQ-A1',
    coat_s1_wave1: '420-680', coat_s1_wave2: '850/940',
    coat_s2_wave1: '420-680', coat_s2_wave2: '850/940',
    coat_s1_ravg1: '0.5', coat_s1_ravg2: '1',
    coat_s2_ravg1: '0.5', coat_s2_ravg2: '1',
    coat_s1_angle1: '0-22', coat_s1_angle2: '0-22',
    coat_s2_angle1: '0-22', coat_s2_angle2: '0-22',
    proc_c_single: '60″', proc_c_assembly: '60″',
    proc_b: '60/40', proc_ranking: '01', signature: 'l.y.h',
    N_mode: 'auto', N_manual: '1.5', DN: '0.3',
    chamfer_mode: 'auto', chamfer_left: '0.1', chamfer_right: '0.3',
    t_tol: '0.02', sag_tol: '0.02',
    dia_tol_pos_upper: '0.010', dia_tol_pos_lower: '0.025',
    dia_tol_nonpos_upper: '0.05', dia_tol_nonpos_lower: '0.10',
    cemented_ref_lens: '2',
  };

  for (const [id, val] of Object.entries(defaults)) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (typeof val === 'boolean') {
      el.checked = val;
    } else {
      el.value = val;
    }
  }

  // Reset lens type to single
  const singleRadio = document.querySelector('input[name="lens_type"][value="single"]');
  if (singleRadio) singleRadio.checked = true;
  updateLensTypeUI();
  updateCAInputs();
  updateNInputs();
  updateChamferInputs();
  refreshPreview();
  showToast('参数已重置', 'success');
}

/* ── CA Mode toggle ── */
function updateCAInputs() {
  const mode = document.getElementById('CA_mode');
  const isAuto = mode ? mode.value === 'auto' : true;
  const ratioRow = document.getElementById('ca-ratio-row');
  const ca1Row = document.getElementById('ca1-manual-row');
  const ca2Row = document.getElementById('ca2-manual-row');

  if (ratioRow) ratioRow.style.display = isAuto ? 'flex' : 'none';
  if (ca1Row) ca1Row.style.display = isAuto ? 'none' : 'flex';
  if (ca2Row) ca2Row.style.display = isAuto ? 'none' : 'flex';
}

/* ── N Mode toggle ── */
function updateNInputs() {
  const mode = document.getElementById('N_mode');
  const isAuto = mode ? mode.value === 'auto' : true;
  const manualRow = document.getElementById('n-manual-row');

  if (manualRow) manualRow.style.display = isAuto ? 'none' : 'flex';
}

/* ── Chamfer Mode toggle ── */
function updateChamferInputs() {
  const mode = document.getElementById('chamfer_mode');
  const isManual = mode && mode.value === 'manual';
  const leftRow = document.getElementById('draw-chamfer-left-row');
  const rightRow = document.getElementById('draw-chamfer-right-row');

  if (leftRow) leftRow.style.display = isManual ? 'flex' : 'none';
  if (rightRow) rightRow.style.display = isManual ? 'flex' : 'none';
}

/* ── Event wiring ── */
function bindEvents() {
  // Parameter inputs → debounced preview
  const paramIds = [
    'T','R1','R2','MD','AD1','AD2',
    'CA1','CA2','part_name','part_no','glass_name',
    'glass2','T2','R3','MD2','AD3',
    'glass3','T3','R4','MD3','AD4',
    'coat_s1_wave1','coat_s1_wave2','coat_s2_wave1','coat_s2_wave2',
    'coat_s1_ravg1','coat_s1_ravg2','coat_s2_ravg1','coat_s2_ravg2',
    'coat_s1_angle1','coat_s1_angle2','coat_s2_angle1','coat_s2_angle2',
    'proc_c_single','proc_c_assembly','proc_b','proc_ranking','signature',
    'N_mode','N_manual','DN',
    'ca_ratio',
    'chamfer_mode','chamfer_left','chamfer_right',
    't_tol','sag_tol',
    'dia_tol_pos_upper','dia_tol_pos_lower','dia_tol_nonpos_upper','dia_tol_nonpos_lower',
    'cemented_ref_lens'
  ];
  paramIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', refreshPreview);
  });

  // Lens type radio buttons
  document.querySelectorAll('input[name="lens_type"]').forEach(radio => {
    radio.addEventListener('change', () => { updateLensTypeUI(); refreshPreview(); });
  });

  // CA mode selector
  const caMode = document.getElementById('CA_mode');
  if (caMode) caMode.addEventListener('change', () => { updateCAInputs(); refreshPreview(); });

  // N mode selector
  const nMode = document.getElementById('N_mode');
  if (nMode) nMode.addEventListener('change', () => { updateNInputs(); refreshPreview(); });

  // Chamfer mode selector
  const chamferMode = document.getElementById('chamfer_mode');
  if (chamferMode) chamferMode.addEventListener('change', () => { updateChamferInputs(); refreshPreview(); });

  // Select elements need 'change' for preview refresh (not just 'input')
  document.querySelectorAll('select').forEach(sel => {
    sel.addEventListener('change', refreshPreview);
  });

  // Toolbar
  els.btnExport.addEventListener('click', openExportModal);
  els.btnReset.addEventListener('click', resetParams);

  // Mouse wheel to switch pages on preview area
  const previewContainer = document.querySelector('.preview-container');
  if (previewContainer) {
    previewContainer.addEventListener('wheel', (e) => {
      if (previewImages.length < 2) return;
      e.preventDefault();
      switchPreviewPage(e.deltaY > 0 ? 1 : -1);
    }, { passive: false });
  }

  // Window resize → realign overlays
  window.addEventListener('resize', () => {
    if (currentFields.length > 0) alignOverlayContainer();
  });

  // Modal
  els.btnCancelExport.addEventListener('click', closeExportModal);
  if (els.btnChoosePath) els.btnChoosePath.addEventListener('click', chooseSavePath);
  els.btnConfirmExport.addEventListener('click', () => {
    const name = els.exportFilename.value.trim();
    if (!name) { showToast('请输入文件名', 'error'); return; }
    const fname = name.endsWith('.pdf') ? name : name + '.pdf';

    let fullpath;
    const chosenPath = els.exportPath ? els.exportPath.value.trim() : '';
    if (chosenPath) {
      fullpath = chosenPath;  // pywebview returns full path including filename
    } else {
      fullpath = fname;  // fallback to current dir
    }
    doExport(fullpath);
  });
  els.exportModal.addEventListener('click', (e) => {
    if (e.target === els.exportModal) closeExportModal();
  });
  els.exportFilename.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') els.btnConfirmExport.click();
    if (e.key === 'Escape') closeExportModal();
  });
}

/* ── Init ── */
async function loadDrawDefaults() {
  try {
    const res = await fetch('/api/settings');
    const settings = await res.json();
    // Map settings keys → draw.html field IDs
    const mapping = {
      proc_c_single: 'proc_c_single',
      proc_c_assembly: 'proc_c_assembly',
      proc_surface_defect: 'proc_b',
      proc_ranking: 'proc_ranking',
      proc_N_mode: 'N_mode',
      proc_N_manual: 'N_manual',
      proc_DN: 'DN',
      proc_signature: 'signature',
      ca_ratio: 'ca_ratio',
      chamfer_mode: 'chamfer_mode',
      chamfer_left: 'chamfer_left',
      chamfer_right: 'chamfer_right',
      t_tol: 't_tol',
      sag_tol: 'sag_tol',
      dia_tol_pos_upper: 'dia_tol_pos_upper',
      dia_tol_pos_lower: 'dia_tol_pos_lower',
      dia_tol_nonpos_upper: 'dia_tol_nonpos_upper',
      dia_tol_nonpos_lower: 'dia_tol_nonpos_lower',
      cemented_ref_lens: 'cemented_ref_lens',
      coat_preset: 'coat_preset',
    };
    for (const [sk, drawId] of Object.entries(mapping)) {
      const el = document.getElementById(drawId);
      if (el && settings[sk] !== undefined) {
        el.value = settings[sk];
      }
    }
    updateCAInputs();
    updateNInputs();
    updateChamferInputs();
  } catch (err) {
    console.warn('加载设置失败，使用默认值:', err.message);
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  if (embeddedMode) {
    // 嵌入模式 UI 调整
    const backBtn = document.querySelector('.back-btn');
    if (backBtn) backBtn.style.display = 'none';
    const btnExport = document.getElementById('btn-export');
    if (btnExport) btnExport.style.display = 'none';
    const btnReset = document.getElementById('btn-reset');
    if (btnReset) btnReset.style.display = 'none';
    // 隐藏镜片参数卡片
    const lensCard = document.getElementById('card-lens-params');
    if (lensCard) lensCard.style.display = 'none';
    // 显示保存并返回按钮
    const btnSaveReturn = document.getElementById('btn-save-return');
    if (btnSaveReturn) {
      btnSaveReturn.style.display = '';
      btnSaveReturn.addEventListener('click', saveAndReturn);
    }
    // 监听父窗口消息
    window.addEventListener('message', onParentMessage);
  }
  await loadDrawDefaults();  // 从通用设置加载初始值
  updateLensTypeUI();
  bindEvents();
  bindOverlayToggle();
  if (!embeddedMode) {
    refreshPreview();  // 嵌入模式等待父窗口发送数据后再刷新
  }
});

/* ── Overlay visibility toggle ── */
function bindOverlayToggle() {
  const btn = document.getElementById('btn-overlay-toggle');
  const container = document.getElementById('preview-container');
  if (!btn || !container) return;

  btn.addEventListener('click', () => {
    const isActive = btn.classList.toggle('active');
    container.classList.toggle('show-overlays', isActive);
    btn.querySelector('span').textContent = isActive ? '隐藏输入框' : '显示输入框';
  });
}

/* ═══════════════════════════════════════════════════════════════════
   EMBEDDED MODE (iframe from batch page)
   ═══════════════════════════════════════════════════════════════════ */

function inferLensTypeFromRow(row) {
  if (row.glass3 && row.T3) return 'triplet';
  if (row.glass2 && row.T2) return 'doublet';
  return 'single';
}

function applyOverrides(overrides) {
  if (!overrides || typeof overrides !== 'object') return;
  // 字段 ID 映射：overrides key → draw.html input ID
  const fieldMap = {
    proc_c_single: 'proc_c_single',
    proc_c_assembly: 'proc_c_assembly',
    proc_b: 'proc_b',
    proc_ranking: 'proc_ranking',
    N_mode: 'N_mode',
    N_manual: 'N_manual',
    DN: 'DN',
    signature: 'signature',
    chamfer_mode: 'chamfer_mode',
    chamfer_left: 'chamfer_left',
    chamfer_right: 'chamfer_right',
    CA_mode: 'CA_mode',
    ca_ratio: 'ca_ratio',
    CA1: 'CA1',
    CA2: 'CA2',
    t_tol: 't_tol',
    sag_tol: 'sag_tol',
    dia_tol_pos_upper: 'dia_tol_pos_upper',
    dia_tol_pos_lower: 'dia_tol_pos_lower',
    dia_tol_nonpos_upper: 'dia_tol_nonpos_upper',
    dia_tol_nonpos_lower: 'dia_tol_nonpos_lower',
    cemented_ref_lens: 'cemented_ref_lens',
    coat_preset: 'coat_preset',
    coat_s1_wave1: 'coat_s1_wave1',
    coat_s1_wave2: 'coat_s1_wave2',
    coat_s2_wave1: 'coat_s2_wave1',
    coat_s2_wave2: 'coat_s2_wave2',
    coat_s1_ravg1: 'coat_s1_ravg1',
    coat_s1_ravg2: 'coat_s1_ravg2',
    coat_s2_ravg1: 'coat_s2_ravg1',
    coat_s2_ravg2: 'coat_s2_ravg2',
    coat_s1_angle1: 'coat_s1_angle1',
    coat_s1_angle2: 'coat_s1_angle2',
    coat_s2_angle1: 'coat_s2_angle1',
    coat_s2_angle2: 'coat_s2_angle2',
  };
  for (const [key, elId] of Object.entries(fieldMap)) {
    if (overrides[key] !== undefined) {
      const el = document.getElementById(elId);
      if (el) el.value = overrides[key];
    }
  }
  // 更新条件显示的 UI 元素
  updateCAInputs();
  updateNInputs();
  updateChamferInputs();
}

function onParentMessage(e) {
  const data = e.data;
  if (!data) return;

  if (data.type === 'batch-row-data') {
    const row = data.payload.row;
    const overrides = data.payload.overrides || {};

    // 设置镜片类型
    const lensType = inferLensTypeFromRow(row);
    const radio = document.querySelector('input[name="lens_type"][value="' + lensType + '"]');
    if (radio) radio.checked = true;
    updateLensTypeUI();

    // 应用已有覆盖参数
    applyOverrides(overrides);

    // 刷新预览
    refreshPreview();
  }

  if (data.type === 'draw-request-save') {
    saveAndReturn();
  }
}

function saveAndReturn() {
  const params = collectParams();
  // 只发送加工相关参数（不包含镜片几何参数）
  const overrides = {
    proc_c_single: params.proc_c_single,
    proc_c_assembly: params.proc_c_assembly,
    proc_b: params.proc_b,
    proc_ranking: params.proc_ranking,
    N_mode: params.N_mode,
    N_manual: params.N_manual,
    DN: params.DN,
    signature: params.signature,
    chamfer_mode: params.chamfer_mode,
    chamfer_left: params.chamfer_left,
    chamfer_right: params.chamfer_right,
    CA_mode: params.CA_mode,
    ca_ratio: params.ca_ratio,
    CA1: params.CA1,
    CA2: params.CA2,
    t_tol: params.t_tol,
    sag_tol: params.sag_tol,
    dia_tol_pos_upper: params.dia_tol_pos_upper,
    dia_tol_pos_lower: params.dia_tol_pos_lower,
    dia_tol_nonpos_upper: params.dia_tol_nonpos_upper,
    dia_tol_nonpos_lower: params.dia_tol_nonpos_lower,
    cemented_ref_lens: params.cemented_ref_lens,
    coat_preset: params.coat_preset,
    coat_s1_wave1: params.coat_s1_wave1,
    coat_s1_wave2: params.coat_s1_wave2,
    coat_s2_wave1: params.coat_s2_wave1,
    coat_s2_wave2: params.coat_s2_wave2,
    coat_s1_ravg1: params.coat_s1_ravg1,
    coat_s1_ravg2: params.coat_s1_ravg2,
    coat_s2_ravg1: params.coat_s2_ravg1,
    coat_s2_ravg2: params.coat_s2_ravg2,
    coat_s1_angle1: params.coat_s1_angle1,
    coat_s1_angle2: params.coat_s1_angle2,
    coat_s2_angle1: params.coat_s2_angle1,
    coat_s2_angle2: params.coat_s2_angle2,
  };
  window.parent.postMessage({ type: 'draw-save', payload: overrides }, '*');
}
