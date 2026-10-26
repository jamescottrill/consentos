/**
 * IAB GPP CMP API 1.1: the `__gpp()` global.
 *
 * Vendors call this to read the visitor's choices under the Global Privacy
 * Platform. Analogous to `__tcfapi()` for TCF v2.3.
 *
 * Commands: ping, addEventListener, removeEventListener, hasSection,
 * getSection and getField. `ping` carries the whole consent state.
 *
 * @see https://github.com/InteractiveAdvertisingBureau/Global-Privacy-Platform/blob/main/Core/CMP%20API%20Specification.md
 */

import { type GppString, type SectionDef, encodeGppString, SECTION_REGISTRY } from './gpp';
import { resolveSections } from './gpp-consent';

// ── Types ────────────────────────────────────────────────────────────

declare global {
  interface Window {
    __gpp?: GppApiFunction;
    __gppQueue?: GppQueueEntry[];
  }
}

/** Signature of the __gpp() global function. */
export type GppApiFunction = (
  command: string,
  callback: GppApiCallback,
  parameter?: unknown,
) => void;

/** Callback passed to __gpp() by callers. */
export type GppApiCallback = (data: unknown, success: boolean) => void;

/** A queued __gpp() call (from the stub). */
export type GppQueueEntry = [string, GppApiCallback, unknown?];

/** One sub-section of a parsed section: its fields by name. */
export type Subsection = Record<string, number | number[] | boolean>;

/** Return type for the 'ping' command. */
export interface GppPingReturn {
  gppVersion: string;
  cmpStatus: 'stub' | 'loading' | 'loaded' | 'error';
  cmpDisplayStatus: 'visible' | 'hidden' | 'disabled';
  signalStatus: 'ready' | 'not ready';
  /** `"<section id>:<API prefix>"`, e.g. `"7:usnat"`. */
  supportedAPIs: string[];
  cmpId: number;
  /** Section IDs in the GPP string. */
  sectionList: number[];
  /** Section IDs that apply to this visitor, or `[-1]` when none do. */
  applicableSections: number[];
  /** The current GPP string, empty until the visitor has chosen. */
  gppString: string;
  /** Each section in the GPP string, keyed by API prefix. */
  parsedSections: Record<string, Subsection[]>;
}

/** Event object sent to listeners. */
export interface GppEventData {
  eventName: string;
  listenerId: number;
  data: unknown;
  pingData: GppPingReturn;
}

// ── Internal state ───────────────────────────────────────────────────

interface GppApiState {
  cmpId: number;
  sections: SectionDef[];
  gpp: GppString | null;
  gppString: string;
  signalStatus: 'ready' | 'not ready';
  displayStatus: 'visible' | 'hidden' | 'disabled';
  listeners: Map<number, GppApiCallback>;
  nextListenerId: number;
}

let apiState: GppApiState | null = null;

// ── Builders ─────────────────────────────────────────────────────────

function parsedSections(gpp: GppString | null): Record<string, Subsection[]> {
  const parsed: Record<string, Subsection[]> = {};
  for (const [id, section] of gpp?.sections ?? []) {
    const def = SECTION_REGISTRY.get(id);
    if (!def) continue;
    const subsections: Subsection[] = [{ ...section.data }];
    if (section.gpcSubsection) {
      subsections.push({ SubsectionType: 1, Gpc: section.gpcSubsection.gpc });
    }
    parsed[def.apiPrefix] = subsections;
  }
  return parsed;
}

function buildPingReturn(state: GppApiState): GppPingReturn {
  const applicable = state.gpp
    ? state.gpp.header.applicableSections
    : state.sections.map((def) => def.id);
  return {
    gppVersion: '1.1',
    cmpStatus: 'loaded',
    cmpDisplayStatus: state.displayStatus,
    signalStatus: state.signalStatus,
    supportedAPIs: state.sections.map((def) => `${def.id}:${def.apiPrefix}`),
    cmpId: state.cmpId,
    sectionList: state.gpp ? [...state.gpp.header.sectionIds] : [],
    applicableSections: applicable.length > 0 ? [...applicable] : [-1],
    gppString: state.gppString,
    parsedSections: parsedSections(state.gpp),
  };
}

function notify(state: GppApiState, eventName: string, data: unknown): void {
  const pingData = buildPingReturn(state);
  state.listeners.forEach((callback, listenerId) => {
    try {
      callback({ eventName, listenerId, data, pingData } satisfies GppEventData, true);
    } catch {
      // A failing listener mustn't stop the others hearing.
    }
  });
}

// ── Command handler ──────────────────────────────────────────────────

function gppApiHandler(command: string, callback: GppApiCallback, parameter?: unknown): void {
  if (!apiState) {
    callback(false, false);
    return;
  }

  switch (command) {
    case 'ping': {
      callback(buildPingReturn(apiState), true);
      break;
    }

    case 'hasSection': {
      const prefix = typeof parameter === 'string' ? parameter : '';
      callback(prefix in parsedSections(apiState.gpp), true);
      break;
    }

    case 'getSection': {
      const prefix = typeof parameter === 'string' ? parameter : '';
      const section = parsedSections(apiState.gpp)[prefix];
      callback(section ?? null, section !== undefined);
      break;
    }

    case 'getField': {
      // "<API prefix>.<field name>", e.g. "usnat.SaleOptOut".
      const [prefix, name] = typeof parameter === 'string' ? parameter.split('.', 2) : [];
      const subsection = parsedSections(apiState.gpp)[prefix]?.find((s) => name in s);
      callback(subsection ? subsection[name] : null, subsection !== undefined);
      break;
    }

    case 'addEventListener': {
      const listenerId = apiState.nextListenerId++;
      apiState.listeners.set(listenerId, callback);
      const event: GppEventData = {
        eventName: 'listenerRegistered',
        listenerId,
        data: true,
        pingData: buildPingReturn(apiState),
      };
      try {
        callback(event, true);
      } catch {
        // Ignore a listener that fails on registration.
      }
      break;
    }

    case 'removeEventListener': {
      const listenerId = parameter as number;
      const removed = apiState.listeners.delete(listenerId);
      const event: GppEventData = {
        eventName: 'listenerRemoved',
        listenerId,
        data: removed,
        pingData: buildPingReturn(apiState),
      };
      callback(event, removed);
      break;
    }

    default:
      callback(null, false);
  }
}

// ── Queue processing ─────────────────────────────────────────────────

/** Process any queued __gpp() calls from the stub installed by the loader. */
function processQueuedCalls(): void {
  if (typeof window === 'undefined') return;

  const queue = window.__gppQueue;
  if (Array.isArray(queue)) {
    for (const entry of queue) {
      gppApiHandler(entry[0], entry[1], entry[2]);
    }
    queue.length = 0;
  }
}

// ── Public API ───────────────────────────────────────────────────────

/**
 * Install the __gpp() global function and process any queued calls.
 *
 * @param cmpId Registered CMP ID.
 * @param supportedApis The site's section prefixes, e.g. `['usnat', 'usca']`.
 */
export function installGppApi(cmpId: number, supportedApis: string[] = []): void {
  apiState = {
    cmpId,
    sections: resolveSections(supportedApis),
    gpp: null,
    gppString: '',
    signalStatus: 'not ready',
    displayStatus: 'hidden',
    listeners: new Map(),
    nextListenerId: 1,
  };

  if (typeof window !== 'undefined') {
    window.__gpp = gppApiHandler;
  }

  processQueuedCalls();
}

/** Remove the __gpp() global function and clear state. */
export function removeGppApi(): void {
  if (typeof window !== 'undefined') {
    delete window.__gpp;
    delete window.__gppQueue;
  }
  apiState = null;
}

/**
 * Record the visitor's choices, hide the banner and tell listeners.
 *
 * Listeners hear, in order: `signalStatus` "not ready" if a string was
 * already set, `cmpDisplayStatus` if the banner was showing, one
 * `sectionChange` per section, then `signalStatus` "ready".
 *
 * @returns The encoded GPP string.
 */
export function updateGppConsent(gpp: GppString): string {
  const gppString = encodeGppString(gpp);
  const state = apiState;
  if (!state) return gppString;

  if (state.signalStatus === 'ready') {
    state.signalStatus = 'not ready';
    notify(state, 'signalStatus', 'not ready');
  }
  state.gpp = gpp;
  state.gppString = gppString;
  setGppDisplayStatus('hidden');
  for (const prefix of Object.keys(parsedSections(gpp))) {
    notify(state, 'sectionChange', prefix);
  }
  state.signalStatus = 'ready';
  notify(state, 'signalStatus', 'ready');

  return gppString;
}

/** Set the display status, telling listeners when it changes. */
export function setGppDisplayStatus(status: 'visible' | 'hidden' | 'disabled'): void {
  if (apiState && apiState.displayStatus !== status) {
    apiState.displayStatus = status;
    notify(apiState, 'cmpDisplayStatus', status);
  }
}

/** Get the current GPP string (empty if no consent yet). */
export function getGppString(): string {
  return apiState?.gppString ?? '';
}

/** Whether the GPP API is installed. */
export function isGppApiInstalled(): boolean {
  return apiState !== null;
}
