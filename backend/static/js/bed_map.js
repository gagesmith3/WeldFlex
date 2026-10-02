// The bed drawing shared by the part designer and the operator's part details:
// the grid, the part's origin corner and its studs. Every helper takes the corner
// and units it works in; the designer wraps them with the part being edited.
//
// The bed is drawn as the operator faces it: front at the bottom, zerozero at
// the bottom-left. A part's X/Y are measured inward from its origin corner, so a
// right corner mirrors X and a back corner mirrors Y. SVG 0,0 is top-left.
//
// The manager swaps the designer partial back in, which runs this file again,
// so BedMap is only defined the first time.
window.BedMap = window.BedMap || (function () {
  const NS = 'http://www.w3.org/2000/svg';
  const BED = 762; // 30in bed, in mm
  const MM_PER_INCH = 25.4;

  // Same keys as part_origin.CORNERS; tests/test_part_origin.py holds them together.
  const CORNERS = ['front_left', 'front_right', 'back_left', 'back_right'];
  const CORNER_LABELS = {
    front_left: 'Front-left',
    front_right: 'Front-right',
    back_left: 'Back-left',
    back_right: 'Back-right',
  };

  function normalizeCorner(value) { return CORNERS.includes(value) ? value : CORNERS[0]; }
  function cornerLabel(corner) { return CORNER_LABELS[normalizeCorner(corner)]; }
  function cornerMirrors(corner) {
    const c = normalizeCorner(corner);
    return { x: c.endsWith('_right'), y: c.startsWith('back_') };
  }

  function toSVG(px, py, corner) {
    const m = cornerMirrors(corner);
    const bedY = m.y ? BED - py : py;
    return { x: m.x ? BED - px : px, y: BED - bedY };
  }
  function toPhys(sx, sy, corner) {
    const m = cornerMirrors(corner);
    const bedY = BED - sy;
    return { x: m.x ? BED - sx : sx, y: m.y ? BED - bedY : bedY };
  }

  function isInches(units) { return units === 'in'; }
  function lengthFactor(units) { return isInches(units) ? MM_PER_INCH : 1; }
  function lengthStep(units) { return isInches(units) ? '0.0001' : '0.001'; }
  function formatLength(mm, units) {
    const decimals = isInches(units) ? 4 : 3;
    return Number((mm / lengthFactor(units)).toFixed(decimals)).toString();
  }
  function unitLabel(units) { return isInches(units) ? 'in' : 'mm'; }

  function svgEl(tag, attrs = {}) {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  }

  // Grid, axis numbers and the part's 0,0 into `g`. `idPrefix` keeps the arrow
  // marker's id unique when a page holds more than one bed.
  function drawGrid(g, { corner, units, idPrefix = 'bed' } = {}) {
    if (!g) return;
    g.innerHTML = '';
    const SIZE = BED, STEP = 50;
    const m = cornerMirrors(corner);

    const gridLines = Array.from(
      { length: Math.floor(SIZE / STEP) + 1 },
      (_, index) => index * STEP,
    );
    if (gridLines.at(-1) !== SIZE) gridLines.push(SIZE);

    for (const i of gridLines) {
      const major = i % 100 === 0;
      const color = major ? '#c8d8e6' : '#e4ecf2';
      const w     = major ? 0.7 : 0.35;

      g.appendChild(svgEl('line', {x1:i, y1:0,    x2:i,    y2:SIZE, stroke:color, 'stroke-width':w}));
      g.appendChild(svgEl('line', {x1:0, y1:i,    x2:SIZE, y2:i,    stroke:color, 'stroke-width':w}));

      // Axis labels count from the part's corner, along the two edges that meet
      // there: X on the front or back edge, Y on the left or right edge.
      if (major && i > 0 && i < SIZE) {
        const tx = svgEl('text', {
          x: i + 4, y: m.y ? 16 : SIZE - 6,
          fill:'#7c95a8', 'font-size':'13', 'font-weight':'600', 'font-family':'monospace',
        });
        tx.textContent = formatLength(m.x ? SIZE - i : i, units);
        g.appendChild(tx);

        const ty = svgEl('text', {
          x: m.x ? SIZE - 4 : 4, y: i - 4, 'text-anchor': m.x ? 'end' : 'start',
          fill:'#7c95a8', 'font-size':'13', 'font-weight':'600', 'font-family':'monospace',
        });
        ty.textContent = formatLength(m.y ? i : SIZE - i, units);
        g.appendChild(ty);
      }
    }

    // Bed border, under the origin arrows that run along it.
    g.appendChild(svgEl('rect', {
      x:0, y:0, width:SIZE, height:SIZE,
      fill:'none', stroke:'#b0c8dc', 'stroke-width':1,
    }));

    // The part's 0,0: the corner it is tooled against, with X and Y running inward.
    // Sized to stay legible on the ~190px kiosk bed without crowding the studs.
    const ORIGIN = '#b4232f', AXIS = 130;
    const arrowId = `${idPrefix}-origin-arrow`;
    const ox = m.x ? SIZE : 0, oy = m.y ? 0 : SIZE;
    const dx = m.x ? -1 : 1, dy = m.y ? 1 : -1;
    const origin = svgEl('g', {opacity:0.75});
    const defs = svgEl('defs');
    const arrow = svgEl('marker', {
      id:arrowId, viewBox:'0 0 10 10', refX:5, refY:5,
      markerWidth:4, markerHeight:4, orient:'auto-start-reverse',
    });
    arrow.appendChild(svgEl('path', {d:'M0 0 10 5 0 10z', fill:ORIGIN}));
    defs.appendChild(arrow);
    origin.appendChild(defs);
    for (const [x2, y2] of [[ox + dx * AXIS, oy], [ox, oy + dy * AXIS]]) {
      origin.appendChild(svgEl('line', {
        x1:ox, y1:oy, x2, y2, stroke:ORIGIN, 'stroke-width':4,
        'marker-end':`url(#${arrowId})`,
      }));
    }
    origin.appendChild(svgEl('circle', {cx:ox, cy:oy, r:10, fill:ORIGIN, stroke:'#ffffff', 'stroke-width':3}));

    const labelAttrs = {fill:ORIGIN, 'font-size':'24', 'font-weight':'600', 'font-family':'monospace'};
    const axisX = svgEl('text', {...labelAttrs, x: ox + dx * (AXIS - 10), y: oy + dy * 32 + 9, 'text-anchor':'middle'});
    axisX.textContent = 'X';
    const axisY = svgEl('text', {...labelAttrs, x: ox + dx * 32, y: oy + dy * (AXIS - 10) + 9, 'text-anchor':'middle'});
    axisY.textContent = 'Y';
    const zero = svgEl('text', {
      ...labelAttrs, x: ox + dx * 28, y: oy + dy * 44 + 9, 'text-anchor': m.x ? 'end' : 'start',
    });
    zero.textContent = '0,0';
    origin.append(axisX, axisY, zero);
    g.appendChild(origin);
  }

  // The travel path into `pathG` and a numbered dot per stud into `ptG`. A stud
  // is labelled with its `id`, or its place in the list. `onStud(g, p)` lets the
  // caller make a dot interactive.
  function drawStuds(pathG, ptG, points, { corner, selectedId = null, onStud = null } = {}) {
    if (!pathG || !ptG) return;
    pathG.innerHTML = '';
    ptG.innerHTML   = '';
    const pts = points || [];

    for (let i = 0; i < pts.length - 1; i++) {
      const a = toSVG(pts[i].x, pts[i].y, corner);
      const b = toSVG(pts[i+1].x, pts[i+1].y, corner);
      pathG.appendChild(svgEl('line', {
        x1:a.x, y1:a.y, x2:b.x, y2:b.y,
        stroke:'#275f84', 'stroke-width':1.2,
        'stroke-dasharray':'5 3', opacity:0.45,
      }));
    }

    pts.forEach((p, index) => {
      const label = p.id ?? index + 1;
      const sv  = toSVG(p.x, p.y, corner);
      const g   = svgEl('g', {'data-pid':label});
      const sel = selectedId !== null && selectedId === p.id;

      if (sel) {
        g.appendChild(svgEl('circle', {cx:sv.x, cy:sv.y, r:11, fill:'none', stroke:'#275f84', 'stroke-width':1.5, opacity:0.35}));
      }
      g.appendChild(svgEl('circle', {
        cx:sv.x, cy:sv.y, r:7,
        fill: sel ? '#275f84' : '#ffffff',
        stroke:'#275f84', 'stroke-width':1.8,
      }));

      const lbl = svgEl('text', {
        x:sv.x, y:sv.y + 4,
        'text-anchor':'middle',
        fill: sel ? '#ffffff' : '#275f84',
        'font-size':'10', 'font-weight':'bold',
        'font-family':'Segoe UI, sans-serif',
        style:'pointer-events:none; user-select:none',
      });
      lbl.textContent = label;
      g.appendChild(lbl);

      if (onStud) onStud(g, p);
      ptG.appendChild(g);
    });
  }

  return {
    BED, MM_PER_INCH, CORNERS, CORNER_LABELS,
    normalizeCorner, cornerLabel, cornerMirrors, toSVG, toPhys,
    isInches, lengthFactor, lengthStep, formatLength, unitLabel,
    svgEl, drawGrid, drawStuds,
  };
})();
