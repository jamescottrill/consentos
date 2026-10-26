/**
 * Tests for the GPP 1.1 CMP API (__gpp() global function).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  installGppApi,
  removeGppApi,
  updateGppConsent,
  setGppDisplayStatus,
  getGppString,
  isGppApiInstalled,
  type GppPingReturn,
  type GppEventData,
  type GppApiCallback,
} from '../gpp-api';
import { buildGppFromConsent } from '../gpp-consent';

// ── Helpers ───────────────────────────────────────────────────────────

/** Call __gpp and return the callback's arguments. __gpp answers at once. */
function callGpp(command: string, parameter?: unknown): { data: unknown; success: boolean } {
  let result: { data: unknown; success: boolean } | undefined;
  window.__gpp!(
    command,
    (data: unknown, success: boolean) => {
      result = { data, success };
    },
    parameter,
  );
  return result!;
}

function ping(): GppPingReturn {
  return callGpp('ping').data as GppPingReturn;
}

/** The visitor rejected marketing, with GPC on. */
function rejected(apis: string[] = ['usnat']) {
  return buildGppFromConsent(['necessary'], apis, true);
}

// ── Tests ─────────────────────────────────────────────────────────────

describe('GPP CMP API', () => {
  beforeEach(() => {
    removeGppApi();
  });

  afterEach(() => {
    removeGppApi();
  });

  describe('installGppApi / removeGppApi', () => {
    it('installs __gpp on window', () => {
      expect(window.__gpp).toBeUndefined();
      installGppApi(42, ['usnat']);
      expect(typeof window.__gpp).toBe('function');
      expect(isGppApiInstalled()).toBe(true);
    });

    it('removes __gpp and its queue from window', () => {
      window.__gppQueue = [['ping', vi.fn()]];
      installGppApi(42);
      removeGppApi();
      expect(window.__gpp).toBeUndefined();
      expect(window.__gppQueue).toBeUndefined();
      expect(isGppApiInstalled()).toBe(false);
    });
  });

  describe('ping', () => {
    it('returns every 1.1 field before the visitor chooses', () => {
      installGppApi(42, ['usnat', 'usca']);
      const { data, success } = callGpp('ping');
      expect(success).toBe(true);
      expect(data).toEqual({
        gppVersion: '1.1',
        cmpStatus: 'loaded',
        cmpDisplayStatus: 'hidden',
        signalStatus: 'not ready',
        supportedAPIs: ['7:usnat', '8:usca'],
        cmpId: 42,
        sectionList: [],
        applicableSections: [7, 8],
        gppString: '',
        parsedSections: {},
      });
    });

    it('lists supported sections in ID order and skips unknown prefixes', () => {
      installGppApi(1, ['usca', 'nonexistent', 'usnat', 'usca']);
      expect(ping().supportedAPIs).toEqual(['7:usnat', '8:usca']);
    });

    it('supports usnat when the site lists no sections, as the encoder does', () => {
      installGppApi(1, []);
      expect(ping().supportedAPIs).toEqual(['7:usnat']);
    });

    it('reports [-1] when no section applies', () => {
      installGppApi(1, ['nonexistent']);
      expect(ping().applicableSections).toEqual([-1]);
    });

    it('carries the consent state once the visitor chooses', () => {
      installGppApi(1, ['usnat', 'usca']);
      const encoded = updateGppConsent(rejected(['usnat', 'usca']));

      const data = ping();
      expect(data.signalStatus).toBe('ready');
      expect(data.gppString).toBe(encoded);
      expect(data.sectionList).toEqual([7, 8]);
      expect(data.applicableSections).toEqual([7, 8]);
      expect(Object.keys(data.parsedSections)).toEqual(['usnat', 'usca']);
    });
  });

  describe('parsedSections', () => {
    it('keys sections by API prefix, with the GPC sub-section after the core', () => {
      installGppApi(1, ['usnat']);
      updateGppConsent(rejected());

      const [core, gpc, ...rest] = ping().parsedSections.usnat;
      expect(core).toMatchObject({ Version: 1, SaleOptOut: 1, SharingNotice: 1 });
      expect(gpc).toEqual({ SubsectionType: 1, Gpc: true });
      expect(rest).toEqual([]);
    });

    it('has only the core for a section without a GPC sub-section', () => {
      installGppApi(1, ['usva']);
      updateGppConsent(rejected(['usva']));
      expect(ping().parsedSections.usva).toHaveLength(1);
    });
  });

  describe('getGPPData', () => {
    it('is gone, as in 1.1', () => {
      installGppApi(1, ['usnat']);
      expect(callGpp('getGPPData')).toEqual({ data: null, success: false });
    });
  });

  describe('getSection', () => {
    it('returns the sub-sections for an API prefix', () => {
      installGppApi(1, ['usnat']);
      updateGppConsent(rejected());
      const { data, success } = callGpp('getSection', 'usnat');
      expect(success).toBe(true);
      expect(data).toEqual(ping().parsedSections.usnat);
    });

    it('returns null before the visitor chooses', () => {
      installGppApi(1, ['usnat']);
      expect(callGpp('getSection', 'usnat')).toEqual({ data: null, success: false });
    });

    it('returns null for a missing or unknown prefix', () => {
      installGppApi(1, ['usnat']);
      updateGppConsent(rejected());
      expect(callGpp('getSection')).toEqual({ data: null, success: false });
      expect(callGpp('getSection', 'nonexistent')).toEqual({ data: null, success: false });
    });
  });

  describe('hasSection', () => {
    it('is true only for a section in the GPP string', () => {
      installGppApi(1, ['usnat']);
      expect(callGpp('hasSection', 'usnat')).toEqual({ data: false, success: true });
      updateGppConsent(rejected());
      expect(callGpp('hasSection', 'usnat')).toEqual({ data: true, success: true });
      expect(callGpp('hasSection', 'usca')).toEqual({ data: false, success: true });
      expect(callGpp('hasSection')).toEqual({ data: false, success: true });
    });
  });

  describe('getField', () => {
    it('returns a core field', () => {
      installGppApi(1, ['usnat']);
      updateGppConsent(rejected());
      expect(callGpp('getField', 'usnat.SaleOptOut')).toEqual({ data: 1, success: true });
    });

    it('returns an array field', () => {
      installGppApi(1, ['usnat']);
      updateGppConsent(rejected());
      const { data } = callGpp('getField', 'usnat.KnownChildSensitiveDataConsents');
      expect(data).toEqual([0, 0]);
    });

    it('returns a GPC sub-section field', () => {
      installGppApi(1, ['usnat']);
      updateGppConsent(rejected());
      expect(callGpp('getField', 'usnat.Gpc')).toEqual({ data: true, success: true });
    });

    it('returns null for an unknown field, section or parameter', () => {
      installGppApi(1, ['usnat']);
      updateGppConsent(rejected());
      expect(callGpp('getField', 'usnat.Nope')).toEqual({ data: null, success: false });
      expect(callGpp('getField', 'usca.SaleOptOut')).toEqual({ data: null, success: false });
      expect(callGpp('getField', 'usnat')).toEqual({ data: null, success: false });
      expect(callGpp('getField')).toEqual({ data: null, success: false });
    });
  });

  describe('event listeners', () => {
    function listen(): GppEventData[] {
      const events: GppEventData[] = [];
      window.__gpp!('addEventListener', (data: unknown) => {
        events.push(data as GppEventData);
      });
      return events;
    }

    it('answers addEventListener with listenerRegistered and true', () => {
      installGppApi(1, ['usnat']);
      const events = listen();
      expect(events).toHaveLength(1);
      expect(events[0]).toMatchObject({ eventName: 'listenerRegistered', listenerId: 1, data: true });
      expect(events[0].pingData.cmpStatus).toBe('loaded');
    });

    it('assigns unique listener IDs', () => {
      installGppApi(1);
      expect(listen()[0].listenerId).toBe(1);
      expect(listen()[0].listenerId).toBe(2);
    });

    it('announces a choice with signalStatus last', () => {
      installGppApi(1, ['usnat', 'usca']);
      setGppDisplayStatus('visible');
      const events = listen();

      updateGppConsent(rejected(['usnat', 'usca']));

      expect(events.slice(1).map((e) => [e.eventName, e.data])).toEqual([
        ['cmpDisplayStatus', 'hidden'],
        ['sectionChange', 'usnat'],
        ['sectionChange', 'usca'],
        ['signalStatus', 'ready'],
      ]);
      expect(events[events.length - 1].pingData.gppString).not.toBe('');
    });

    it('starts with signalStatus "not ready" when the visitor changes their choice', () => {
      installGppApi(1, ['usnat']);
      updateGppConsent(rejected());
      const events = listen();

      updateGppConsent(buildGppFromConsent(['necessary', 'marketing'], ['usnat'], false));

      const names = events.slice(1).map((e) => [e.eventName, e.data]);
      expect(names[0]).toEqual(['signalStatus', 'not ready']);
      expect(names[names.length - 1]).toEqual(['signalStatus', 'ready']);
    });

    it('carries the state at the time in pingData', () => {
      installGppApi(1, ['usnat']);
      const events = listen();
      updateGppConsent(rejected());
      const sectionChange = events.find((e) => e.eventName === 'sectionChange')!;
      expect(sectionChange.pingData.signalStatus).toBe('not ready');
      expect(sectionChange.pingData.parsedSections.usnat).toBeDefined();
    });

    it('keeps telling other listeners when one throws', () => {
      installGppApi(1, ['usnat']);
      const throwing: GppApiCallback = () => {
        throw new Error('Listener error');
      };
      expect(() => window.__gpp!('addEventListener', throwing)).not.toThrow();
      const events = listen();
      expect(() => updateGppConsent(rejected())).not.toThrow();
      expect(events[events.length - 1].eventName).toBe('signalStatus');
    });

    it('answers removeEventListener with listenerRemoved', () => {
      installGppApi(1);
      const listenerId = listen()[0].listenerId;
      const { data, success } = callGpp('removeEventListener', listenerId);
      expect(success).toBe(true);
      expect(data).toMatchObject({ eventName: 'listenerRemoved', listenerId, data: true });
    });

    it('reports false when removing a listener that does not exist', () => {
      installGppApi(1);
      const { data, success } = callGpp('removeEventListener', 999);
      expect(success).toBe(false);
      expect(data).toMatchObject({ eventName: 'listenerRemoved', data: false });
    });

    it('stops telling a removed listener', () => {
      installGppApi(1, ['usnat']);
      const events = listen();
      callGpp('removeEventListener', events[0].listenerId);
      updateGppConsent(rejected());
      expect(events).toHaveLength(1);
    });
  });

  describe('setGppDisplayStatus', () => {
    it('updates ping and tells listeners only when it changes', () => {
      installGppApi(1);
      const events: GppEventData[] = [];
      window.__gpp!('addEventListener', (data: unknown) => events.push(data as GppEventData));

      setGppDisplayStatus('visible');
      setGppDisplayStatus('visible');
      expect(ping().cmpDisplayStatus).toBe('visible');
      expect(events.filter((e) => e.eventName === 'cmpDisplayStatus')).toHaveLength(1);
    });

    it('does nothing when the API is not installed', () => {
      expect(() => setGppDisplayStatus('visible')).not.toThrow();
    });
  });

  describe('updateGppConsent', () => {
    it('returns the encoded GPP string and keeps it', () => {
      installGppApi(1, ['usnat', 'usca']);
      const result = updateGppConsent(rejected(['usnat', 'usca']));
      expect(result.startsWith('D')).toBe(true);
      expect(result.split('~')).toHaveLength(3);
      expect(getGppString()).toBe(result);
    });

    it('still encodes when the API is not installed', () => {
      expect(updateGppConsent(rejected())).toBeTruthy();
      expect(getGppString()).toBe('');
    });
  });

  describe('unknown commands', () => {
    it('fail', () => {
      installGppApi(1);
      expect(callGpp('unknownCommand')).toEqual({ data: null, success: false });
    });
  });

  describe('queue processing', () => {
    it('answers calls queued by the stub, then empties the queue', () => {
      const cb = vi.fn();
      window.__gppQueue = [['ping', cb]];
      installGppApi(1, ['usnat']);
      expect(cb).toHaveBeenCalledWith(expect.objectContaining({ cmpStatus: 'loaded' }), true);
      expect(window.__gppQueue).toEqual([]);
    });
  });

  describe('coexistence', () => {
    it('leaves __tcfapi alone', () => {
      const mockTcf = vi.fn();
      (window as { __tcfapi?: unknown }).__tcfapi = mockTcf;
      installGppApi(1);
      removeGppApi();
      expect((window as { __tcfapi?: unknown }).__tcfapi).toBe(mockTcf);
      delete (window as { __tcfapi?: unknown }).__tcfapi;
    });
  });
});
