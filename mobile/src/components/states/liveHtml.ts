/**
 * What a camera's view on the phone says while it has no picture.
 *
 * The phone shows a camera's live view as the server sends it, frame by frame,
 * in a web view - the server's masked picture and no other. A bare <img> fed by
 * that stream shows black until the first frame and black when the stream could
 * not be opened, which is also what a camera in a dark room looks like. These
 * are the pieces of the page that tell those apart, as the web's `LiveImage`
 * does: "Connecting" until there is a frame, "No picture" when there could not
 * be one, and "Try again".
 *
 * They only ever look at whether the element has a picture. They never read the
 * picture, and they ask for the address the page was given and no other.
 */

/** How often a view that is still connecting looks for its first frame. */
export const LOOK_EVERY_MS = 400

/** What a view with one camera tells the screen: it has a frame, or there could not be one. */
export type PictureNews = 'playing' | 'failed'

/**
 * For a page with one picture, `<img id="live">`: the script, put after it,
 * that tells the screen (`onMessage`) when the first frame is there or the
 * stream could not be opened - once each. The screen says the rest, over the
 * web view, and asks again by loading the page afresh.
 *
 * The size of the first frame is known as soon as it has been read. Whether an
 * <img> fed by a stream that never ends says "load" differs from one browser to
 * the next, and a view must never go on saying "Connecting" over a picture that
 * is there - so it is looked for, until it is found.
 */
export const PICTURE_WATCH_SCRIPT = `<script>
(function () {
  var img = document.getElementById('live');
  if (!img) return;
  var told = '';
  function tell(what) {
    if (told === what) return;
    told = what;
    if (window.ReactNativeWebView) window.ReactNativeWebView.postMessage(what);
  }
  // A frame read, or a stream that ended with none - which is also how a failure that came before this
  // script ran is found.
  var timer = setInterval(function () {
    if (img.naturalWidth > 0) { clearInterval(timer); tell('playing'); }
    else if (img.complete) { clearInterval(timer); tell('failed'); }
  }, ${LOOK_EVERY_MS});
  img.addEventListener('load', function () { clearInterval(timer); tell('playing'); });
  img.addEventListener('error', function () { clearInterval(timer); tell('failed'); });
})();
</script>`

/** What a web view's message means, when it is one of the two this page sends; anything else is ignored. */
export function pictureNews(data: unknown): PictureNews | null {
  return data === 'playing' || data === 'failed' ? data : null
}

/**
 * For a wall of tiles: the style of each tile's own state - drawn over the
 * tile, cheaply, and gone the moment the tile has a frame. `still` (a phone set
 * to remove animations) keeps the small ring from turning.
 */
export function tileStateCss(still: boolean): string {
  return `
    .state {
      position: absolute; top: 0; left: 0; right: 0; bottom: 0;
      display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px;
      background: #05070f; color: rgba(248,250,252,0.85); font: 700 12px sans-serif;
    }
    .tile.playing .state { display: none; }
    .spin {
      width: 18px; height: 18px; border-radius: 50%;
      border: 2px solid rgba(248,250,252,0.22); border-top-color: rgba(248,250,252,0.72);
      animation: ${still ? 'none' : 'turn 0.9s linear infinite'};
    }
    @keyframes turn { to { transform: rotate(360deg); } }
    .tile.failed .spin { display: none; }
    .again {
      display: none; min-height: 36px; padding: 0 14px; border-radius: 8px;
      border: 1px solid rgba(248,250,252,0.5); background: transparent; color: #f8fafc; font: 600 12px sans-serif;
    }
    .tile.failed .again { display: block; }`
}

/** The state that sits in each tile, after its <img>, until the tile has a frame. */
export const TILE_STATE_HTML =
  '<div class="state" role="status"><div class="spin"></div><span class="words">Connecting</span>' +
  '<button type="button" class="again">Try again</button></div>'

/**
 * The script, put at the end of the wall's page, that runs each tile's state.
 * "Try again" puts a new <img> in the tile with the same address, so the
 * browser opens a new connection; what the old one says afterwards is not
 * listened to.
 */
export const TILE_WATCH_SCRIPT = `<script>
(function () {
  var tiles = document.querySelectorAll('.tile');
  Array.prototype.forEach.call(tiles, function (tile) {
    var words = tile.querySelector('.words');
    var timer = null;
    var turn = 0;
    function set(state) {
      tile.className = 'tile ' + state;
      words.textContent = state === 'failed' ? 'No picture' : 'Connecting';
    }
    function watch(img) {
      var mine = ++turn;
      clearInterval(timer);
      set('connecting');
      timer = setInterval(function () {
        if (mine !== turn) return;
        if (img.naturalWidth > 0) { clearInterval(timer); set('playing'); }
        else if (img.complete) { clearInterval(timer); set('failed'); }
      }, ${LOOK_EVERY_MS});
      img.addEventListener('load', function () { if (mine === turn) { clearInterval(timer); set('playing'); } });
      img.addEventListener('error', function () { if (mine === turn) { clearInterval(timer); set('failed'); } });
    }
    tile.querySelector('.again').addEventListener('click', function () {
      var old = tile.querySelector('img');
      var fresh = document.createElement('img');
      watch(fresh);
      fresh.src = old.getAttribute('src');
      old.parentNode.replaceChild(fresh, old);
    });
    watch(tile.querySelector('img'));
  });
})();
</script>`
