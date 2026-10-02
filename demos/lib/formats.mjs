/*
 * Output formats: frame size and where things go. Visual styling is in themes/*.css.
 * Boxes are in output pixels. The captured UI is 1920x1080 device pixels and is scaled into the screen box.
 */
export const FPS = 30;

export const FORMATS = {
  landscape: {
    width: 1920, height: 1080, suffix: '1920x1080',
    screen: { x: 192, y: 26, w: 1536, h: 864 },
    band: { x: 192, y: 904, w: 1536, h: 160, direction: 'row', justify: 'flex-start', captionAlign: 'left', padRight: 300 },
    /* speed and skip badges sit outside the captured UI, so they never cover part of it */
    badges: { right: 192, top: 922 },
    header: null,
    card: { avatar: 520, gap: 40, direction: 'row', align: 'left', items: 'flex-start', textW: 980 },
    fs: { caption: 40, chapter: 22, badge: 22, callout: 30, kicker: 26, cardTitle: 104, cardSub: 38, cardLine: 34, header: 0 },
    termFont: 19, termBar: 46, safeX: 140,
  },
  portrait: {
    width: 1080, height: 1350, suffix: '1080x1350',
    screen: { x: 24, y: 262, w: 1032, h: 774 },
    band: { x: 56, y: 1052, w: 968, h: 270, direction: 'column', justify: 'center', captionAlign: 'center', padRight: 0 },
    badges: { right: 24, top: 1042 },
    header: { y: 40, h: 206 },
    card: { avatar: 560, gap: 10, direction: 'column', align: 'center', items: 'center', textW: 940 },
    fs: { caption: 44, chapter: 22, badge: 22, callout: 30, kicker: 24, cardTitle: 92, cardSub: 36, cardLine: 32, header: 60 },
    termFont: 15, termBar: 40, safeX: 70,
  },
};

export function formatVars(f) {
  const px = (v) => `${v}px`;
  return {
    '--W': px(f.width), '--H': px(f.height),
    '--sx': px(f.screen.x), '--sy': px(f.screen.y), '--sw': px(f.screen.w), '--sh': px(f.screen.h),
    '--bx': px(f.band.x), '--by': px(f.band.y), '--bw': px(f.band.w), '--bh': px(f.band.h),
    '--band-pad-right': px(f.band.padRight), '--badge-right': px(f.badges.right), '--badge-top': px(f.badges.top),
    '--band-direction': f.band.direction, '--band-justify': f.band.justify, '--caption-align': f.band.captionAlign,
    '--hy': px(f.header?.y ?? 0), '--hh': px(f.header?.h ?? 0), '--header-display': f.header ? 'flex' : 'none',
    '--card-avatar': px(f.card.avatar), '--card-gap': px(f.card.gap), '--card-direction': f.card.direction,
    '--card-align': f.card.align, '--card-items': f.card.items, '--card-text-w': px(f.card.textW),
    '--fs-caption': px(f.fs.caption), '--fs-chapter': px(f.fs.chapter), '--fs-badge': px(f.fs.badge),
    '--fs-callout': px(f.fs.callout), '--fs-kicker': px(f.fs.kicker), '--fs-card-title': px(f.fs.cardTitle),
    '--fs-card-sub': px(f.fs.cardSub), '--fs-card-line': px(f.fs.cardLine), '--fs-header': px(f.fs.header),
    '--term-bar-h': px(f.termBar), '--term-pad': '18px 22px', '--safeX': px(f.safeX),
  };
}
