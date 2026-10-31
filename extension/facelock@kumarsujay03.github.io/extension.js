// Face unlock on the lock and login screens.
//
// Face runs as its own GDM service, "gdm-face" (/etc/pam.d/gdm-face ->
// pam_face -> facelockd), next to password and fingerprint: whichever
// succeeds first unlocks.
//
// - It scans only when you wake the screen (any key, mouse or touchpad
//   input after a moment of no input, or the lid/suspend wake), never while
//   the screen is dark and untouched.
// - facelockd reports progress as "facelock:<signal>" PAM info messages;
//   they drive the face badge here instead of text under the password field.
// - In the dark ("facelock:dark") the screen turns white at full brightness
//   for a few seconds to light your face, then goes back.
// - A failed face scan never resets or blocks the password prompt.
// - Esc during a scan stops it (camera off at once) until the next wake.
// - The white light comes only after a first try in the room's own light
//   (facelockd waits light_after seconds), and at most once until the screen
//   has been off again.
// - The icon: with the Fingerprint Icon extension, its badge is the one icon
//   on screen and shows face unlock too (globalThis.__facelockBus carries the
//   face events to it); without it, this extension shows its own face badge.

import Cairo from 'cairo';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Graphene from 'gi://Graphene';
import St from 'gi://St';

import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Batch from 'resource:///org/gnome/shell/gdm/batch.js';
import * as GdmUtil from 'resource:///org/gnome/shell/gdm/util.js';
import {EventEmitter} from 'resource:///org/gnome/shell/misc/signals.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {wiggle} from 'resource:///org/gnome/shell/misc/animationUtils.js';

const FACE_SERVICE = 'gdm-face';
// facelockd's stop-only socket
const CONTROL_SOCKET = '/run/facelock/control';
// what a scan that ends without unlocking tells you
const STOP_TEXT = {
    failures: 'Use fingerprint or password',
    paused: 'Face unlock paused: use fingerprint or password',
    camera: 'Camera unavailable',
    reenroll: 'Face unlock needs a new enrolment',
    antispoof: 'Face unlock needs an update: sudo facelock update',
};
const PREFIX = 'facelock:';

const ICON_SIZE = 34;
const TOP_OFFSET = 4;        // just below the top bar (only shown without the fingerprint badge)
const FADE_TIME = 250;
const PULSE_TIME = 1200;
const MESSAGE_TIME = 2500;
const WIGGLE = {duration: 65, wiggleCount: 3};

// a lock screen starts scanning on input after this much quiet...
const IDLE_BEFORE_WAKE = 1500;
// ...but not in the first moments after locking (the key press that locked)
const LOCK_GRACE = 2000;
// a verification that starts this soon after a wake also starts face
const WANT_WINDOW = 4000;
// the login screen scans when it first shows (you just booted or logged out)
const GREETER_WINDOW = 20000;
// the white light never stays longer than this
const LIGHT_MAX = 6000;
// a scan cut off by the lock screen restarting its sign-in check is started
// again on the new one, at most this many times per wake
const MAX_RESUMES = 2;
const LIGHT_IN = 450;        // the white light fades and brightens in...
const LIGHT_OUT = 700;       // ...and back out, gently
// wakes right after a face unlock are the unlock itself, not a new wake
const AFTER_SUCCESS = 3000;

// shared with the Fingerprint Icon extension (created by whichever loads first)
function faceBus() {
    globalThis.__facelockBus ??= new EventEmitter();
    return globalThis.__facelockBus;
}

function trace(message) {
    console.log(`facelock: ${message}`);
}

function now() {
    return GLib.get_monotonic_time() / 1000;
}

// Fallback icon when the Fingerprint Icon extension isn't there: brackets
// around a simple face that winks on success.
class FaceBadge {
    constructor() {
        this._state = 'idle';
        this._progress = 0; // corner brackets: 0 = open, 1 = closed in
        this.actor = new St.BoxLayout({
            style_class: 'facelock-badge',
            orientation: Clutter.Orientation.VERTICAL,
            opacity: 0,
            visible: false,
        });
        this._icon = new St.DrawingArea({
            width: ICON_SIZE, height: ICON_SIZE,
            x_align: Clutter.ActorAlign.CENTER,
            pivot_point: new Graphene.Point({x: 0.5, y: 0.5}),
        });
        this._icon.connect('repaint', area => this._repaint(area));
        this._label = new St.Label({
            style_class: 'facelock-label',
            x_align: Clutter.ActorAlign.CENTER,
            opacity: 0,
        });
        this.actor.add_child(this._icon);
        this.actor.add_child(this._label);
        this.actor.accessible_name = 'Face unlock';
        Main.layoutManager.uiGroup.add_child(this.actor);
        this._monitorsId = Main.layoutManager.connect('monitors-changed', () => this._place());
        this.actor.connect('notify::width', () => this._place());
        this._place();
    }

    _place() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        this.actor.set_position(
            Math.round(monitor.x + (monitor.width - this.actor.width) / 2),
            monitor.y + Main.layoutManager.panelBox.height + TOP_OFFSET);
    }

    set onLight(on) {
        if (on)
            this.actor.add_style_class_name('on-light');
        else
            this.actor.remove_style_class_name('on-light');
        this._icon.queue_repaint();
    }

    _repaint(area) {
        const cr = area.get_context();
        const [w, h] = area.get_surface_size();
        const dark = this.actor.has_style_class_name('on-light');
        const [r, g, b] = this._state === 'error' ? [1, 0.48, 0.39]
            : dark ? [0.12, 0.12, 0.12] : [1, 1, 1];
        const s = Math.min(w, h), line = Math.max(1.6, s * 0.07);
        try {
            cr.setLineWidth(line);
            cr.setLineCap(Cairo.LineCap.ROUND);
            cr.setSourceRGBA(r, g, b, 0.95);
            // corner brackets close in while scanning
            const inset = line + s * 0.08 * this._progress, arm = s * 0.22;
            for (const [cx, cy, dx, dy] of [[inset, inset, 1, 1], [w - inset, inset, -1, 1],
                [inset, h - inset, 1, -1], [w - inset, h - inset, -1, -1]]) {
                cr.moveTo(cx, cy + dy * arm);
                cr.lineTo(cx, cy);
                cr.lineTo(cx + dx * arm, cy);
                cr.stroke();
            }
            const mid = w / 2;
            const eyeY = h / 2 - s * 0.08, eyeR = s * 0.045;
            cr.arc(mid - s * 0.12, eyeY, eyeR, 0, 2 * Math.PI);
            cr.fill();
            if (this._state === 'success') {
                // the right eye winks
                cr.moveTo(mid + s * 0.07, eyeY);
                cr.lineTo(mid + s * 0.17, eyeY);
                cr.stroke();
            } else {
                cr.arc(mid + s * 0.12, eyeY, eyeR, 0, 2 * Math.PI);
                cr.fill();
            }
            if (this._state === 'error')
                cr.arc(mid, h / 2 + s * 0.22, s * 0.14, 1.15 * Math.PI, 1.85 * Math.PI);
            else
                cr.arc(mid, h / 2 + s * 0.02, s * 0.15, 0.2 * Math.PI, 0.8 * Math.PI);
            cr.stroke();
        } finally {
            cr.$dispose();
        }
    }

    _setState(state) {
        this._state = state;
        this._icon.queue_repaint();
    }

    _show() {
        if (!this.actor.visible) {
            this.actor.show();
            this._icon.set_scale(0.7, 0.7);
            this._icon.ease({
                scale_x: 1, scale_y: 1,
                duration: FADE_TIME * 2,
                mode: Clutter.AnimationMode.EASE_OUT_BACK,
            });
        }
        Main.layoutManager.uiGroup.set_child_above_sibling(this.actor, null);
        this.actor.remove_all_transitions();
        this.actor.ease({opacity: 255, duration: FADE_TIME});
    }

    _pulse() {
        this._icon.remove_transition('opacity');
        this._icon.opacity = 160;
        this._icon.ease({
            opacity: 255,
            duration: PULSE_TIME,
            mode: Clutter.AnimationMode.EASE_IN_OUT_SINE,
            repeatCount: -1,
            autoReverse: true,
        });
    }

    setLabel(text, sticky = false) {
        this._label.remove_all_transitions();
        if (this._labelId) {
            GLib.source_remove(this._labelId);
            this._labelId = 0;
        }
        if (!text) {
            this._label.ease({opacity: 0, duration: FADE_TIME});
            return;
        }
        this._label.text = text;
        this._label.ease({opacity: 255, duration: FADE_TIME});
        if (sticky)
            return;
        this._labelId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, MESSAGE_TIME, () => {
            this._labelId = 0;
            this._label.ease({opacity: 0, duration: FADE_TIME});
            return GLib.SOURCE_REMOVE;
        });
    }

    scanning() {
        this._setState('scanning');
        this._progress = 0;
        this._show();
        this._pulse();
        this.setLabel(null);
        this._animateProgress(1);
    }

    _animateProgress(target) {
        // bracket animation driven by a timeline-free tick
        if (this._progressId)
            GLib.source_remove(this._progressId);
        const start = this._progress, t0 = now(), span = 450;
        this._progressId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 16, () => {
            const k = Math.min(1, (now() - t0) / span);
            this._progress = start + (target - start) * (1 - (1 - k) ** 3);
            this._icon.queue_repaint();
            if (k < 1)
                return GLib.SOURCE_CONTINUE;
            this._progressId = 0;
            return GLib.SOURCE_REMOVE;
        });
    }

    retry(text) {
        this._setState('error');
        this._show();
        wiggle(this._icon, WIGGLE);
        this.setLabel(text);
        if (this._retryId)
            GLib.source_remove(this._retryId);
        this._retryId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 700, () => {
            this._retryId = 0;
            if (this._state === 'error')
                this._setState('scanning');
            return GLib.SOURCE_REMOVE;
        });
    }

    succeeded() {
        this._setState('success');
        this._icon.remove_transition('opacity');
        this._icon.opacity = 255;
        this.setLabel(null);
        this._show();
    }

    // scan over without an unlock: say why, then fade away
    stopped(text) {
        if (this._state === 'success')
            return;
        this._icon.remove_transition('opacity');
        if (text) {
            this._setState('error');
            this._show();
            this.setLabel(text, true);
        }
        if (this._hideId)
            GLib.source_remove(this._hideId);
        this._hideId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, text ? MESSAGE_TIME + 500 : 0, () => {
            this._hideId = 0;
            if (this._state !== 'scanning' && this._state !== 'success')
                this.hide();
            return GLib.SOURCE_REMOVE;
        });
    }

    hide() {
        this._state = 'idle';
        this.setLabel(null);
        this._icon.remove_all_transitions();
        this.actor.remove_all_transitions();
        this.actor.ease({
            opacity: 0,
            duration: FADE_TIME,
            onComplete: () => this.actor.hide(),
        });
    }

    vanish() {
        this.actor.remove_all_transitions();
        this.actor.opacity = 0;
        this.actor.hide();
        this._state = 'idle';
    }

    destroy() {
        for (const id of [this._labelId, this._retryId, this._hideId, this._progressId]) {
            if (id)
                GLib.source_remove(id);
        }
        Main.layoutManager.disconnect(this._monitorsId);
        this.actor.destroy();
    }
}

// Full-screen white at full brightness, to light a face in a dark room. It
// fades in while the backlight rises, and both ease back out.
class FaceLight {
    constructor() {
        this.actor = new St.Widget({
            style_class: 'facelock-light',
            reactive: false,
            opacity: 0,
            visible: false,
        });
        Main.layoutManager.uiGroup.add_child(this.actor);
    }

    get on() {
        return this._saved !== undefined;
    }

    // move the backlight smoothly from its current value to `target`
    _rampBrightness(target, duration) {
        if (this._rampId) {
            GLib.source_remove(this._rampId);
            this._rampId = 0;
        }
        const scale = Main.brightnessManager?.globalScale;
        if (!scale || target === null)
            return;
        const from = scale.value, t0 = now();
        this._rampId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 16, () => {
            let k = Math.min(1, (now() - t0) / duration);
            const eased = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
            try {
                scale.value = from + (target - from) * eased;
            } catch (e) {
                console.error(`facelock: brightness: ${e}`);
                k = 1;
            }
            if (k < 1)
                return GLib.SOURCE_CONTINUE;
            this._rampId = 0;
            return GLib.SOURCE_REMOVE;
        });
    }

    turnOn() {
        if (this.on)
            return false;
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return false;
        trace('dark: lighting the face with the screen');
        this.actor.set_position(monitor.x, monitor.y);
        this.actor.set_size(monitor.width, monitor.height);
        Main.layoutManager.uiGroup.set_child_above_sibling(this.actor, null);
        this.actor.show();
        this.actor.remove_all_transitions();
        this.actor.ease({
            opacity: 255,
            duration: LIGHT_IN,
            mode: Clutter.AnimationMode.EASE_IN_OUT_SINE,
        });
        const scale = Main.brightnessManager?.globalScale;
        this._saved = scale ? scale.value : null;
        this._rampBrightness(1.0, LIGHT_IN);
        this._maxId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, LIGHT_MAX, () => {
            this._maxId = 0;
            if (this.turnOff())
                this.onTimeout?.();
            return GLib.SOURCE_REMOVE;
        });
        return true;
    }

    // `quick`: the screen is unlocking, so don't hold it up
    turnOff(quick = false) {
        if (this._saved === undefined)
            return false;
        if (this._maxId) {
            GLib.source_remove(this._maxId);
            this._maxId = 0;
        }
        const duration = quick ? FADE_TIME : LIGHT_OUT;
        this._rampBrightness(this._saved, duration);
        this._saved = undefined;
        this.actor.remove_all_transitions();
        this.actor.ease({
            opacity: 0,
            duration,
            mode: Clutter.AnimationMode.EASE_IN_OUT_SINE,
            onComplete: () => this.actor.hide(),
        });
        return true;
    }

    destroy() {
        this.turnOff(true);
        if (this._rampId)
            GLib.source_remove(this._rampId);
        this.actor.destroy();
    }
}

// Decides when face scans run and shows what they're doing.
class FaceController {
    constructor() {
        this._verifiers = new Set();
        this._wantUntil = Main.sessionMode.isGreeter ? now() + GREETER_WINDOW : 0;
        this._paused = false;
        this._badge = new FaceBadge();
        this._light = new FaceLight();
        // the light went out by itself: the icon must know (it draws dark
        // ink on the white light)
        this._light.onTimeout = () => this._show('light', false);
        this._idle = global.backend.get_core_idle_monitor();
        this._watchIds = [];

        Main.screenShield?.connectObject(
            'locked-changed', () => this._onLockedChanged(),
            'wake-up-screen', () => {
                // back from a dark screen: the white light may help once more
                this._litOnce = false;
                this._onWake('screen woke');
            },
            this);
        global.stage.connectObject('captured-event', (_stage, event) => {
            if (event.type() === Clutter.EventType.KEY_PRESS &&
                event.get_key_symbol() === Clutter.KEY_Escape && faceBus().scanning)
                this._escape();
            return Clutter.EVENT_PROPAGATE;
        }, this);
        if (Main.screenShield?.locked || Main.sessionMode.isGreeter)
            this._armAfter(Main.sessionMode.isGreeter ? 0 : LOCK_GRACE);
    }

    get _active() {
        return Main.sessionMode.isGreeter || Main.screenShield?.locked;
    }

    // Show a face state on the one icon: the Fingerprint Icon badge when it
    // is listening, otherwise this extension's own badge.
    //   scanning | retry(n, max) | blink | matched | stopped(reason) | light(on)
    _show(state, ...args) {
        const bus = faceBus();
        if (bus.displays > 0) {
            this._badge.vanish();
            bus.emit('face', state, ...args);
            return;
        }
        const badge = this._badge;
        switch (state) {
        case 'scanning':
            badge.scanning();
            break;
        case 'retry':
            badge.retry(args[0] >= args[1] ? 'Not recognised' : `Not recognised (${args[0]} of ${args[1]})`);
            break;
        case 'blink':
            badge.setLabel('Blink to unlock', true);
            break;
        case 'matched':
            badge.succeeded();
            break;
        case 'stopped':
            badge.stopped(args[0] === 'paused' && args[1] > 0
                ? `Face unlock paused for ${args[1]} s: use fingerprint or password`
                : STOP_TEXT[args[0]] ?? null);
            break;
        case 'light':
            badge.onLight = args[0];
            if (args[0])
                Main.layoutManager.uiGroup.set_child_above_sibling(badge.actor, null);
            break;
        }
    }

    _lightOn() {
        if (this._light.turnOn())
            this._show('light', true);
    }

    _lightOff(quick = false) {
        if (this._light.turnOff(quick))
            this._show('light', false);
    }

    _onLockedChanged() {
        if (Main.screenShield.locked) {
            trace('locked');
            this._paused = false;
            this._litOnce = false;
            this._armAfter(LOCK_GRACE);
        } else {
            trace('unlocked');
            faceBus().scanning = false;
            this._clearWatches();
            this._paused = false;
            this._lightOff(true);
            this._badge.vanish();
        }
    }

    _clearWatches() {
        for (const id of this._watchIds)
            this._idle.remove_watch(id);
        this._watchIds = [];
        if (this._graceId) {
            GLib.source_remove(this._graceId);
            this._graceId = 0;
        }
    }

    // wait for a moment of no input, then for the next input
    _armAfter(delay) {
        this._clearWatches();
        this._graceId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, Math.max(delay, 1), () => {
            this._graceId = 0;
            this._armIdle();
            return GLib.SOURCE_REMOVE;
        });
    }

    _armIdle() {
        if (!this._active)
            return;
        // the idle watch fires every time the session goes quiet, so it is
        // removed once it has fired: one wake watch at a time
        const idleId = this._idle.add_idle_watch(IDLE_BEFORE_WAKE, () => {
            this._idle.remove_watch(idleId);
            this._watchIds = this._watchIds.filter(id => id !== idleId);
            const activeId = this._idle.add_user_active_watch(() => {
                this._watchIds = this._watchIds.filter(id => id !== activeId);
                this._onWake('input after a pause');
                this._armIdle();
            });
            this._watchIds.push(activeId);
        });
        this._watchIds.push(idleId);
    }

    // Esc during a scan: stop it now (camera off), light off, fingerprint
    // icon back; the Esc itself doesn't count as a wake
    _escape() {
        trace('Esc: stopping the face scan');
        this._escapedAt = now();
        this._wantUntil = 0;
        this._lightOff(true);
        try {
            const client = new Gio.SocketClient();
            client.connect_async(Gio.UnixSocketAddress.new(CONTROL_SOCKET), null, (c, res) => {
                try {
                    const conn = c.connect_finish(res);
                    conn.get_output_stream().write_all(new TextEncoder().encode('STOP\n'), null);
                    conn.close(null);
                } catch (e) {
                    console.error(`facelock: could not stop the scan: ${e}`);
                }
            });
        } catch (e) {
            console.error(`facelock: could not stop the scan: ${e}`);
        }
    }

    _onWake(why) {
        if (!this._active || now() - (this._succeededAt ?? -1e9) < AFTER_SUCCESS)
            return;
        if (now() - (this._escapedAt ?? -1e9) < IDLE_BEFORE_WAKE)
            return;
        // (while face unlock is paused the service answers at once, and the
        // icon shows why, so every wake still asks it)
        trace(`${why}: scanning`);
        this._wantUntil = now() + WANT_WINDOW;
        this._resumes = 0;
        let started = false;
        for (const verifier of this._verifiers)
            started = this.maybeStart(verifier) || started;
        if (!started && !Main.sessionMode.isGreeter)
            this._beginBehindClock();
    }

    // the lock screen runs no verification until its prompt exists (or after
    // the screen blanked and cancelled it); (re)start it, still showing the
    // clock, so the face service can start
    _beginBehindClock() {
        const dialog = Main.screenShield?._dialog;
        if (!dialog?._ensureAuthPrompt)
            return;
        try {
            dialog._ensureAuthPrompt();
            if (dialog._activePage !== dialog._promptBox)
                dialog.grab_key_focus();
        } catch (e) {
            console.error(`facelock: ${e}`);
        }
    }

    register(verifier) {
        this._verifiers.add(verifier);
    }

    forget(verifier) {
        this._verifiers.delete(verifier);
    }

    maybeStart(verifier) {
        if (!this._active || this._paused || now() > this._wantUntil)
            return false;
        if (!verifier._userVerifier || !verifier._userName || !verifier._hold)
            return false;
        if (verifier._activeServices.has(FACE_SERVICE) || verifier._faceRunning)
            return true;
        verifier._faceRunning = true;
        this._wantUntil = 0; // one scan per wake
        faceBus().scanning = true;
        trace(`starting face scan for ${verifier._userName}`);
        // GNOME's hold for this verification is released once its own
        // services started; a later start needs a fresh one (as its _retry does)
        if (!verifier._hold.isAcquired())
            verifier._hold = new Batch.Hold();
        verifier._startService(FACE_SERVICE).catch(e => {
            verifier._faceRunning = false;
            verifier._activeServices?.delete(FACE_SERVICE);
            console.error(`facelock: could not start ${FACE_SERVICE}: ${e}`);
        });
        return true;
    }

    onMessage(verifier, text) {
        if (!text?.startsWith(PREFIX))
            return;
        const [signal, ...rest] = text.slice(PREFIX.length).split(':');
        switch (signal) {
        case 'scanning':
            this._show('scanning');
            break;
        case 'dark':
            // once until the screen has been off; never after Esc
            if (!this._litOnce && now() - (this._escapedAt ?? -1e9) > WANT_WINDOW) {
                this._litOnce = true;
                this._lightOn();
            }
            break;
        case 'light':
            this._lightOff();
            break;
        case 'blink':
            this._show('blink');
            break;
        case 'retry': {
            const [n, max] = rest.map(Number);
            this._show('retry', n, max);
            break;
        }
        case 'matched':
            faceBus().scanning = false;
            this._succeededAt = now();
            this._lightOff(true);
            this._show('matched');
            break;
        case 'paused':
            // seconds left of a short pause, or 0: until another unlock
            this._lightOff();
            this._show('stopped', 'paused', Number(rest[0] ?? 0));
            break;
        case 'failed': {
            this._lightOff();
            const reason = rest.join(':');
            this._show('stopped', reason.startsWith('camera') ? 'camera' : reason);
            break;
        }
        }
    }

    // the lock screen restarted its sign-in check under a running scan (the
    // screen blanking, a prompt reset): put the light out, and scan again on
    // the next check if this wake is recent
    onCutOff(verifier) {
        faceBus().scanning = false;
        this._lightOff(true);
        if (!this._active || this._paused || (this._resumes ?? 0) >= MAX_RESUMES ||
            now() - (this._succeededAt ?? -1e9) < AFTER_SUCCESS ||
            now() - (this._escapedAt ?? -1e9) < WANT_WINDOW)
            return;
        this._resumes = (this._resumes ?? 0) + 1;
        trace('face scan cut off by a restarted sign-in check; scanning again');
        this._wantUntil = now() + WANT_WINDOW;
        this._show('scanning');
    }

    onStopped(verifier) {
        faceBus().scanning = false;
        verifier._faceRunning = false;
        this._lightOff();
        this._show('light', this._light.on); // keep the icon's ink in step
        if (now() - (this._succeededAt ?? -1e9) > AFTER_SUCCESS)
            this._show('stopped', 'ended');
    }

    onSucceeded() {
        if (now() - (this._succeededAt ?? -1e9) > AFTER_SUCCESS) {
            this._succeededAt = now();
            this._show('matched');
        }
        this._lightOff(true);
    }

    vanish() {
        this._lightOff(true);
        this._badge.vanish();
    }

    destroy() {
        this._clearWatches();
        Main.screenShield?.disconnectObject(this);
        global.stage.disconnectObject(this);
        this._light.destroy();
        this._badge.destroy();
        this._verifiers.clear();
    }
}

export default class FaceUnlockExtension extends Extension {
    enable() {
        this._controller = new FaceController();
        this._injections = new InjectionManager();
        const controller = this._controller;
        const proto = GdmUtil.ShellUserVerifier.prototype;
        const safely = fn => {
            try {
                fn();
            } catch (e) {
                console.error(`facelock: ${e}`);
            }
        };

        // face messages drive the badge, never the text under the password
        for (const name of ['_onInfo', '_onProblem']) {
            this._injections.overrideMethod(proto, name, original =>
                function (client, serviceName, text) {
                    if (serviceName !== FACE_SERVICE)
                        return original.call(this, client, serviceName, text);
                    safely(() => controller.onMessage(this, text));
                    return undefined;
                });
        }

        // a finished face scan must not count as a failed login or restart:
        // the next scan waits for the next wake
        this._injections.overrideMethod(proto, '_onConversationStopped', original =>
            function (client, serviceName) {
                if (serviceName !== FACE_SERVICE)
                    return original.call(this, client, serviceName);
                this._activeServices.delete(FACE_SERVICE);
                safely(() => controller.onStopped(this));
                return undefined;
            });

        this._injections.overrideMethod(proto, '_onServiceUnavailable', original =>
            function (client, serviceName, errorMessage) {
                if (serviceName !== FACE_SERVICE)
                    return original.call(this, client, serviceName, errorMessage);
                trace(`face service unavailable: ${errorMessage}`);
                safely(() => controller.onStopped(this));
                return undefined;
            });

        this._injections.overrideMethod(proto, '_onVerificationComplete', original =>
            function (client, serviceName) {
                if (serviceName === FACE_SERVICE) {
                    trace('face recognised');
                    safely(() => controller.onSucceeded());
                }
                return original.call(this, client, serviceName);
            });

        // a verification is ready: start face too, if the screen just woke
        this._injections.overrideMethod(proto, '_startBackgroundServices', original =>
            function (...args) {
                const result = original.apply(this, args);
                safely(() => {
                    controller.register(this);
                    controller.maybeStart(this);
                });
                return result;
            });

        for (const name of ['clear', 'destroy']) {
            this._injections.overrideMethod(proto, name, original =>
                function (...args) {
                    safely(() => {
                        // GNOME disconnects from GDM before the conversation
                        // ends, so a running face scan never reports back
                        if (this._faceRunning)
                            controller.onCutOff(this);
                        this._faceRunning = false;
                        controller.forget(this);
                    });
                    return original.apply(this, args);
                });
        }
    }

    disable() {
        // "unlock-dialog" is in session-modes so face unlock keeps working on
        // the lock screen; it is only disabled for real
        this._injections.clear();
        this._injections = null;
        this._controller.destroy();
        this._controller = null;
    }
}
