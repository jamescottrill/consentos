/**
 * Tests for the IAB GPP string encoder/decoder.
 */

import { describe, it, expect } from 'vitest';
import { BitWriter, BitReader, bytesToBase64url } from '../tcf';
import {
  fibonacciEncode,
  fibonacciDecode,
  encodeGppHeader,
  decodeGppHeader,
  encodeSectionCore,
  decodeSectionCore,
  encodeGpcSubsection,
  decodeGpcSubsection,
  encodeSection,
  decodeSection,
  encodeGppString,
  decodeGppString,
  createDefaultSectionData,
  getSectionByPrefix,
  registerSection,
  US_NATIONAL,
  US_CALIFORNIA,
  US_VIRGINIA,
  US_COLORADO,
  US_CONNECTICUT,
  US_FLORIDA,
  SECTION_REGISTRY,
  GppFieldValue,
  type SectionDef,
  type SectionData,
  type GppHeader,
  type GppString,
} from '../gpp';

// ── Fibonacci coding ─────────────────────────────────────────────────

describe('Fibonacci coding', () => {
  it('encodes and decodes 1', () => {
    const writer = new BitWriter();
    fibonacciEncode(writer, 1);
    const bytes = writer.toBytes();
    const reader = new BitReader(bytes);
    expect(fibonacciDecode(reader)).toBe(1);
  });

  it('encodes and decodes 2', () => {
    const writer = new BitWriter();
    fibonacciEncode(writer, 2);
    const bytes = writer.toBytes();
    const reader = new BitReader(bytes);
    expect(fibonacciDecode(reader)).toBe(2);
  });

  it('encodes and decodes small integers (1–20)', () => {
    for (let n = 1; n <= 20; n++) {
      const writer = new BitWriter();
      fibonacciEncode(writer, n);
      const bytes = writer.toBytes();
      const reader = new BitReader(bytes);
      expect(fibonacciDecode(reader)).toBe(n);
    }
  });

  it('encodes and decodes larger integers', () => {
    const values = [21, 34, 55, 100, 233, 500, 987, 1000];
    for (const n of values) {
      const writer = new BitWriter();
      fibonacciEncode(writer, n);
      const bytes = writer.toBytes();
      const reader = new BitReader(bytes);
      expect(fibonacciDecode(reader)).toBe(n);
    }
  });

  it('encodes multiple integers sequentially', () => {
    const values = [7, 3, 11, 1, 8];
    const writer = new BitWriter();
    for (const v of values) {
      fibonacciEncode(writer, v);
    }
    const bytes = writer.toBytes();
    const reader = new BitReader(bytes);
    for (const v of values) {
      expect(fibonacciDecode(reader)).toBe(v);
    }
  });

  it('throws on zero or negative values', () => {
    const writer = new BitWriter();
    expect(() => fibonacciEncode(writer, 0)).toThrow('positive integer');
    expect(() => fibonacciEncode(writer, -1)).toThrow('positive integer');
  });

  it('produces correct bit pattern for value 1 (11)', () => {
    const writer = new BitWriter();
    fibonacciEncode(writer, 1);
    // Value 1: position 0 set (F(2)=1), then terminator → bits: 1,1
    const bytes = writer.toBytes();
    // First byte: 11xxxxxx → 0b11000000 = 192
    expect(bytes[0] & 0xc0).toBe(0xc0);
  });

  it('produces correct bit pattern for value 2 (011)', () => {
    const writer = new BitWriter();
    fibonacciEncode(writer, 2);
    // Value 2: position 1 set (F(3)=2), then terminator → bits: 0,1,1
    const bytes = writer.toBytes();
    // First byte: 011xxxxx → 0b01100000 = 96
    expect(bytes[0] & 0xe0).toBe(0x60);
  });

  it('produces correct bit pattern for value 4 (1011)', () => {
    const writer = new BitWriter();
    fibonacciEncode(writer, 4);
    // Value 4 = F(2)+F(4) = 1+3: positions 0,2 set, then terminator → bits: 1,0,1,1
    const bytes = writer.toBytes();
    // First byte: 1011xxxx → 0b10110000 = 176
    expect(bytes[0] & 0xf0).toBe(0xb0);
  });
});

// ── GPP Header ───────────────────────────────────────────────────────

describe('GPP header', () => {
  it('encodes and decodes an empty header', () => {
    const header: GppHeader = {
      version: 1,
      sectionIds: [],
      applicableSections: [],
    };
    const encoded = encodeGppHeader(header);
    const decoded = decodeGppHeader(encoded);

    expect(decoded.version).toBe(1);
    expect(decoded.sectionIds).toEqual([]);
    expect(decoded.applicableSections).toEqual([]);
  });

  it('encodes and decodes a header with a single section', () => {
    const header: GppHeader = {
      version: 1,
      sectionIds: [7],
      applicableSections: [7],
    };
    const encoded = encodeGppHeader(header);
    const decoded = decodeGppHeader(encoded);

    expect(decoded.version).toBe(1);
    expect(decoded.sectionIds).toEqual([7]);
    expect(decoded.applicableSections).toEqual([7]);
  });

  it('encodes and decodes a header with multiple sections', () => {
    const header: GppHeader = {
      version: 1,
      sectionIds: [7, 8, 10],
      applicableSections: [7, 8],
    };
    const encoded = encodeGppHeader(header);
    const decoded = decodeGppHeader(encoded);

    expect(decoded.version).toBe(1);
    expect(decoded.sectionIds).toEqual([7, 8, 10]);
    // Applicable sections aren't encoded, so decoding takes every section.
    expect(decoded.applicableSections).toEqual([7, 8, 10]);
  });

  // Test vectors from the IAB reference library (iabgpp-es HeaderV1 tests).
  it.each([
    [[], 'DBAA'],
    [[2], 'DBABMA'],
    [[2, 6], 'DBACNYA'],
  ])('encodes section IDs %j as the specification does', (sectionIds, expected) => {
    expect(encodeGppHeader({ version: 1, sectionIds, applicableSections: [] })).toBe(expected);
    expect(decodeGppHeader(expected).sectionIds).toEqual(sectionIds);
  });

  it('encodes consecutive section IDs as ranges', () => {
    const header: GppHeader = {
      version: 1,
      sectionIds: [7, 8, 9, 10, 11],
      applicableSections: [7, 8, 9, 10, 11],
    };
    const encoded = encodeGppHeader(header);
    const decoded = decodeGppHeader(encoded);

    expect(decoded.sectionIds).toEqual([7, 8, 9, 10, 11]);
    expect(decoded.applicableSections).toEqual([7, 8, 9, 10, 11]);
  });

  it('handles mixed consecutive and non-consecutive IDs', () => {
    const header: GppHeader = {
      version: 1,
      sectionIds: [7, 8, 9, 11, 14],
      applicableSections: [7],
    };
    const encoded = encodeGppHeader(header);
    const decoded = decodeGppHeader(encoded);

    expect(decoded.sectionIds).toEqual([7, 8, 9, 11, 14]);
  });

  it('throws on invalid header type', () => {
    // Craft a header with type = 0 instead of 3
    const writer = new BitWriter();
    writer.writeInt(0, 6); // Wrong type
    writer.writeInt(1, 6);
    writer.writeInt(0, 6);
    writer.writeInt(0, 6);
    const encoded = bytesToBase64url(writer.toBytes());
    expect(() => decodeGppHeader(encoded)).toThrow('Invalid GPP header type');
  });

  it('round-trips: encode → decode → re-encode produces identical output', () => {
    const header: GppHeader = {
      version: 1,
      sectionIds: [7, 8, 11, 14],
      applicableSections: [7, 8],
    };
    const encoded1 = encodeGppHeader(header);
    const decoded = decodeGppHeader(encoded1);
    const encoded2 = encodeGppHeader(decoded);
    expect(encoded2).toBe(encoded1);
  });
});

// ── Section encoding/decoding ────────────────────────────────────────

describe('US National section (Section 7)', () => {
  it('encodes and decodes default (all-zero) data', () => {
    const data = createDefaultSectionData(US_NATIONAL);
    const encoded = encodeSectionCore(US_NATIONAL, data);
    const decoded = decodeSectionCore(US_NATIONAL, encoded);

    expect(decoded.Version).toBe(1);
    expect(decoded.SharingNotice).toBe(0);
    expect(decoded.SaleOptOut).toBe(0);
    expect(decoded.SensitiveDataProcessing).toEqual(new Array(12).fill(0));
    expect(decoded.KnownChildSensitiveDataConsents).toEqual([0, 0]);
  });

  it('encodes and decodes populated data', () => {
    const data: SectionData = {
      Version: 1,
      SharingNotice: 1,
      SaleOptOutNotice: 1,
      SharingOptOutNotice: 1,
      TargetedAdvertisingOptOutNotice: 1,
      SensitiveDataProcessingOptOutNotice: 1,
      SensitiveDataLimitUseNotice: 1,
      SaleOptOut: 2,
      SharingOptOut: 1,
      TargetedAdvertisingOptOut: 2,
      SensitiveDataProcessing: [1, 2, 0, 1, 2, 0, 1, 2, 0, 1, 2, 0],
      KnownChildSensitiveDataConsents: [1, 2],
      PersonalDataConsents: 0,
      MspaCoveredTransaction: 1,
      MspaOptOutOptionMode: 1,
      MspaServiceProviderMode: 0,
    };
    const encoded = encodeSectionCore(US_NATIONAL, data);
    const decoded = decodeSectionCore(US_NATIONAL, encoded);

    expect(decoded.Version).toBe(1);
    expect(decoded.SharingNotice).toBe(1);
    expect(decoded.SaleOptOut).toBe(2);
    expect(decoded.SharingOptOut).toBe(1);
    expect(decoded.TargetedAdvertisingOptOut).toBe(2);
    expect(decoded.SensitiveDataProcessing).toEqual([1, 2, 0, 1, 2, 0, 1, 2, 0, 1, 2, 0]);
    expect(decoded.KnownChildSensitiveDataConsents).toEqual([1, 2]);
    expect(decoded.MspaCoveredTransaction).toBe(1);
  });

  it('round-trips core data', () => {
    const data: SectionData = {
      Version: 1,
      SharingNotice: 2,
      SaleOptOutNotice: 1,
      SharingOptOutNotice: 2,
      TargetedAdvertisingOptOutNotice: 1,
      SensitiveDataProcessingOptOutNotice: 2,
      SensitiveDataLimitUseNotice: 1,
      SaleOptOut: 1,
      SharingOptOut: 2,
      TargetedAdvertisingOptOut: 1,
      SensitiveDataProcessing: [2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1],
      KnownChildSensitiveDataConsents: [2, 1],
      PersonalDataConsents: 2,
      MspaCoveredTransaction: 2,
      MspaOptOutOptionMode: 2,
      MspaServiceProviderMode: 2,
    };
    const encoded1 = encodeSectionCore(US_NATIONAL, data);
    const decoded = decodeSectionCore(US_NATIONAL, encoded1);
    const encoded2 = encodeSectionCore(US_NATIONAL, decoded);
    expect(encoded2).toBe(encoded1);
  });
});

describe('US California section (Section 8)', () => {
  it('encodes and decodes default data', () => {
    const data = createDefaultSectionData(US_CALIFORNIA);
    const encoded = encodeSectionCore(US_CALIFORNIA, data);
    const decoded = decodeSectionCore(US_CALIFORNIA, encoded);

    expect(decoded.Version).toBe(1);
    expect(decoded.SaleOptOutNotice).toBe(0);
    expect(decoded.SensitiveDataProcessing).toEqual(new Array(9).fill(0));
    expect(decoded.KnownChildSensitiveDataConsents).toEqual([0, 0]);
  });

  it('encodes and decodes populated data', () => {
    const data: SectionData = {
      Version: 1,
      SaleOptOutNotice: 1,
      SharingOptOutNotice: 1,
      SensitiveDataLimitUseNotice: 2,
      SaleOptOut: 1,
      SharingOptOut: 1,
      SensitiveDataProcessing: [1, 1, 2, 2, 0, 0, 1, 1, 2],
      KnownChildSensitiveDataConsents: [1, 0],
      PersonalDataConsents: 1,
      MspaCoveredTransaction: 1,
      MspaOptOutOptionMode: 0,
      MspaServiceProviderMode: 2,
    };
    const encoded = encodeSectionCore(US_CALIFORNIA, data);
    const decoded = decodeSectionCore(US_CALIFORNIA, encoded);

    expect(decoded.SaleOptOut).toBe(1);
    expect(decoded.SharingOptOut).toBe(1);
    expect(decoded.SensitiveDataProcessing).toEqual([1, 1, 2, 2, 0, 0, 1, 1, 2]);
    expect(decoded.MspaServiceProviderMode).toBe(2);
  });

  it('round-trips core data', () => {
    const data: SectionData = {
      Version: 1,
      SaleOptOutNotice: 2,
      SharingOptOutNotice: 1,
      SensitiveDataLimitUseNotice: 1,
      SaleOptOut: 2,
      SharingOptOut: 2,
      SensitiveDataProcessing: [2, 2, 1, 1, 0, 0, 2, 2, 1],
      KnownChildSensitiveDataConsents: [2, 2],
      PersonalDataConsents: 2,
      MspaCoveredTransaction: 1,
      MspaOptOutOptionMode: 1,
      MspaServiceProviderMode: 1,
    };
    const e1 = encodeSectionCore(US_CALIFORNIA, data);
    const d = decodeSectionCore(US_CALIFORNIA, e1);
    const e2 = encodeSectionCore(US_CALIFORNIA, d);
    expect(e2).toBe(e1);
  });
});

describe('US Virginia section (Section 9)', () => {
  it('encodes and decodes default data', () => {
    const data = createDefaultSectionData(US_VIRGINIA);
    const encoded = encodeSectionCore(US_VIRGINIA, data);
    const decoded = decodeSectionCore(US_VIRGINIA, encoded);

    expect(decoded.Version).toBe(1);
    expect(decoded.SensitiveDataProcessing).toEqual(new Array(8).fill(0));
    expect(decoded.KnownChildSensitiveDataConsents).toBe(0);
  });

  it('round-trips core data', () => {
    const data: SectionData = {
      Version: 1,
      SharingNotice: 1,
      SaleOptOutNotice: 2,
      TargetedAdvertisingOptOutNotice: 1,
      SaleOptOut: 2,
      TargetedAdvertisingOptOut: 1,
      SensitiveDataProcessing: [1, 2, 1, 2, 1, 2, 1, 2],
      KnownChildSensitiveDataConsents: 1,
      MspaCoveredTransaction: 2,
      MspaOptOutOptionMode: 1,
      MspaServiceProviderMode: 2,
    };
    const e1 = encodeSectionCore(US_VIRGINIA, data);
    const d = decodeSectionCore(US_VIRGINIA, e1);
    const e2 = encodeSectionCore(US_VIRGINIA, d);
    expect(e2).toBe(e1);
  });

  it('does not support GPC sub-section', () => {
    expect(US_VIRGINIA.hasGpcSubsection).toBe(false);
  });
});

describe('US Colorado section (Section 10)', () => {
  it('encodes and decodes default data', () => {
    const data = createDefaultSectionData(US_COLORADO);
    const encoded = encodeSectionCore(US_COLORADO, data);
    const decoded = decodeSectionCore(US_COLORADO, encoded);

    expect(decoded.Version).toBe(1);
    expect(decoded.SensitiveDataProcessing).toEqual(new Array(7).fill(0));
  });

  it('round-trips core data', () => {
    const data: SectionData = {
      Version: 1,
      SharingNotice: 2,
      SaleOptOutNotice: 1,
      TargetedAdvertisingOptOutNotice: 2,
      SaleOptOut: 1,
      TargetedAdvertisingOptOut: 2,
      SensitiveDataProcessing: [2, 1, 0, 2, 1, 0, 2],
      KnownChildSensitiveDataConsents: 1,
      MspaCoveredTransaction: 1,
      MspaOptOutOptionMode: 2,
      MspaServiceProviderMode: 0,
    };
    const e1 = encodeSectionCore(US_COLORADO, data);
    const d = decodeSectionCore(US_COLORADO, e1);
    const e2 = encodeSectionCore(US_COLORADO, d);
    expect(e2).toBe(e1);
  });

  it('supports GPC sub-section', () => {
    expect(US_COLORADO.hasGpcSubsection).toBe(true);
  });
});

describe('US Connecticut section (Section 11)', () => {
  it('encodes and decodes default data', () => {
    const data = createDefaultSectionData(US_CONNECTICUT);
    const encoded = encodeSectionCore(US_CONNECTICUT, data);
    const decoded = decodeSectionCore(US_CONNECTICUT, encoded);

    expect(decoded.Version).toBe(1);
    expect(decoded.SensitiveDataProcessing).toEqual(new Array(8).fill(0));
    expect(decoded.KnownChildSensitiveDataConsents).toEqual([0, 0, 0]);
  });

  it('round-trips core data', () => {
    const data: SectionData = {
      Version: 1,
      SharingNotice: 1,
      SaleOptOutNotice: 1,
      TargetedAdvertisingOptOutNotice: 1,
      SaleOptOut: 1,
      TargetedAdvertisingOptOut: 1,
      SensitiveDataProcessing: [1, 1, 1, 1, 1, 1, 1, 1],
      KnownChildSensitiveDataConsents: [1, 2, 1],
      MspaCoveredTransaction: 2,
      MspaOptOutOptionMode: 0,
      MspaServiceProviderMode: 0,
    };
    const e1 = encodeSectionCore(US_CONNECTICUT, data);
    const d = decodeSectionCore(US_CONNECTICUT, e1);
    const e2 = encodeSectionCore(US_CONNECTICUT, d);
    expect(e2).toBe(e1);
  });
});

describe('US Florida section (Section 14)', () => {
  it('encodes and decodes default data', () => {
    const data = createDefaultSectionData(US_FLORIDA);
    const encoded = encodeSectionCore(US_FLORIDA, data);
    const decoded = decodeSectionCore(US_FLORIDA, encoded);

    expect(decoded.Version).toBe(1);
    expect(decoded.SensitiveDataProcessing).toEqual(new Array(8).fill(0));
    expect(decoded.KnownChildSensitiveDataConsents).toEqual([0, 0, 0]);
    expect(decoded.PersonalDataConsents).toBe(0);
  });

  it('round-trips core data', () => {
    const data: SectionData = {
      Version: 1,
      SharingNotice: 2,
      SaleOptOutNotice: 2,
      TargetedAdvertisingOptOutNotice: 2,
      SaleOptOut: 2,
      TargetedAdvertisingOptOut: 2,
      SensitiveDataProcessing: [2, 2, 2, 2, 2, 2, 2, 2],
      KnownChildSensitiveDataConsents: [2, 2, 2],
      PersonalDataConsents: 2,
      MspaCoveredTransaction: 2,
      MspaOptOutOptionMode: 2,
      MspaServiceProviderMode: 2,
    };
    const e1 = encodeSectionCore(US_FLORIDA, data);
    const d = decodeSectionCore(US_FLORIDA, e1);
    const e2 = encodeSectionCore(US_FLORIDA, d);
    expect(e2).toBe(e1);
  });

  it('supports GPC sub-section', () => {
    expect(US_FLORIDA.hasGpcSubsection).toBe(true);
  });
});

// ── GPC sub-section ──────────────────────────────────────────────────

describe('GPC sub-section', () => {
  it('encodes and decodes gpc=true', () => {
    const encoded = encodeGpcSubsection({ gpc: true });
    const decoded = decodeGpcSubsection(encoded);
    expect(decoded.gpc).toBe(true);
  });

  it('encodes and decodes gpc=false', () => {
    const encoded = encodeGpcSubsection({ gpc: false });
    const decoded = decodeGpcSubsection(encoded);
    expect(decoded.gpc).toBe(false);
  });

  it('round-trips: encode → decode → re-encode is identical', () => {
    const gpc = { gpc: true };
    const e1 = encodeGpcSubsection(gpc);
    const d = decodeGpcSubsection(e1);
    const e2 = encodeGpcSubsection(d);
    expect(e2).toBe(e1);
  });
});

// ── Full section with GPC sub-section ────────────────────────────────

describe('encodeSection / decodeSection (core + GPC)', () => {
  it('encodes section without GPC when not provided', () => {
    const data = createDefaultSectionData(US_NATIONAL);
    const encoded = encodeSection(US_NATIONAL, data);
    expect(encoded).not.toContain('.');
  });

  it('encodes section with GPC sub-section', () => {
    const data = createDefaultSectionData(US_NATIONAL);
    const encoded = encodeSection(US_NATIONAL, data, { gpc: true });
    expect(encoded).toContain('.');
  });

  it('decodes section with GPC sub-section', () => {
    const data: SectionData = {
      ...createDefaultSectionData(US_NATIONAL),
      SaleOptOut: 1,
      SharingOptOut: 1,
    };
    const encoded = encodeSection(US_NATIONAL, data, { gpc: true });
    const { data: decoded, gpcSubsection } = decodeSection(US_NATIONAL, encoded);

    expect(decoded.SaleOptOut).toBe(1);
    expect(decoded.SharingOptOut).toBe(1);
    expect(gpcSubsection).toBeDefined();
    expect(gpcSubsection!.gpc).toBe(true);
  });

  it('ignores GPC sub-section for sections that do not support it', () => {
    const data = createDefaultSectionData(US_VIRGINIA);
    const encoded = encodeSection(US_VIRGINIA, data, { gpc: true });
    // Virginia doesn't support GPC — should not append sub-section
    expect(encoded).not.toContain('.');

    const { gpcSubsection } = decodeSection(US_VIRGINIA, encoded);
    expect(gpcSubsection).toBeUndefined();
  });

  it('round-trips section with GPC', () => {
    const data: SectionData = {
      ...createDefaultSectionData(US_CALIFORNIA),
      SaleOptOut: 2,
      SharingOptOut: 1,
    };
    const gpc = { gpc: false };
    const e1 = encodeSection(US_CALIFORNIA, data, gpc);
    const { data: d, gpcSubsection } = decodeSection(US_CALIFORNIA, e1);
    const e2 = encodeSection(US_CALIFORNIA, d, gpcSubsection);
    expect(e2).toBe(e1);
  });
});

// ── Full GPP string ──────────────────────────────────────────────────

describe('full GPP string encoding/decoding', () => {
  it('encodes and decodes a single US National section', () => {
    const gpp: GppString = {
      header: {
        version: 1,
        sectionIds: [7],
        applicableSections: [7],
      },
      sections: new Map([
        [7, { data: createDefaultSectionData(US_NATIONAL), gpcSubsection: { gpc: false } }],
      ]),
    };

    const encoded = encodeGppString(gpp);
    expect(encoded).toContain('~');

    const decoded = decodeGppString(encoded);
    expect(decoded.header.sectionIds).toEqual([7]);
    expect(decoded.sections.has(7)).toBe(true);
    expect(decoded.sections.get(7)!.data.Version).toBe(1);
    expect(decoded.sections.get(7)!.gpcSubsection?.gpc).toBe(false);
  });

  it('encodes and decodes multiple sections', () => {
    const gpp: GppString = {
      header: {
        version: 1,
        sectionIds: [7, 8, 10],
        applicableSections: [8],
      },
      sections: new Map([
        [7, {
          data: {
            ...createDefaultSectionData(US_NATIONAL),
            SaleOptOut: 1,
            SharingOptOut: 1,
          },
          gpcSubsection: { gpc: true },
        }],
        [8, {
          data: {
            ...createDefaultSectionData(US_CALIFORNIA),
            SaleOptOut: 1,
          },
          gpcSubsection: { gpc: true },
        }],
        [10, {
          data: {
            ...createDefaultSectionData(US_COLORADO),
            TargetedAdvertisingOptOut: 2,
          },
          gpcSubsection: { gpc: false },
        }],
      ]),
    };

    const encoded = encodeGppString(gpp);
    const parts = encoded.split('~');
    expect(parts.length).toBe(4); // header + 3 sections

    const decoded = decodeGppString(encoded);
    expect(decoded.header.sectionIds).toEqual([7, 8, 10]);
    expect(decoded.header.applicableSections).toEqual([7, 8, 10]);
    expect(decoded.sections.get(7)!.data.SaleOptOut).toBe(1);
    expect(decoded.sections.get(7)!.gpcSubsection?.gpc).toBe(true);
    expect(decoded.sections.get(8)!.data.SaleOptOut).toBe(1);
    expect(decoded.sections.get(10)!.data.TargetedAdvertisingOptOut).toBe(2);
  });

  it('round-trips a complex GPP string', () => {
    const gpp: GppString = {
      header: {
        version: 1,
        sectionIds: [7, 8, 9, 10, 11, 14],
        applicableSections: [7, 8],
      },
      sections: new Map([
        [7, {
          data: {
            ...createDefaultSectionData(US_NATIONAL),
            SharingNotice: 1,
            SaleOptOutNotice: 1,
            SaleOptOut: 1,
            SharingOptOut: 1,
            TargetedAdvertisingOptOut: 1,
            SensitiveDataProcessing: [1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2],
            KnownChildSensitiveDataConsents: [0, 0],
            MspaCoveredTransaction: 1,
          },
          gpcSubsection: { gpc: true },
        }],
        [8, {
          data: {
            ...createDefaultSectionData(US_CALIFORNIA),
            SaleOptOut: 1,
            SharingOptOut: 1,
            SensitiveDataProcessing: [2, 2, 2, 2, 2, 2, 2, 2, 2],
          },
          gpcSubsection: { gpc: true },
        }],
        [9, {
          data: {
            ...createDefaultSectionData(US_VIRGINIA),
            SaleOptOut: 2,
          },
        }],
        [10, {
          data: createDefaultSectionData(US_COLORADO),
          gpcSubsection: { gpc: false },
        }],
        [11, {
          data: createDefaultSectionData(US_CONNECTICUT),
          gpcSubsection: { gpc: false },
        }],
        [14, {
          data: createDefaultSectionData(US_FLORIDA),
          gpcSubsection: { gpc: true },
        }],
      ]),
    };

    const encoded1 = encodeGppString(gpp);
    const decoded = decodeGppString(encoded1);
    const encoded2 = encodeGppString(decoded);
    expect(encoded2).toBe(encoded1);
  });

  it('throws when section data is missing', () => {
    const gpp: GppString = {
      header: {
        version: 1,
        sectionIds: [7, 8],
        applicableSections: [],
      },
      sections: new Map([
        [7, { data: createDefaultSectionData(US_NATIONAL) }],
        // Section 8 intentionally missing
      ]),
    };

    expect(() => encodeGppString(gpp)).toThrow('No data or definition for GPP section 8');
  });

  it('throws when section payload is missing during decode', () => {
    // Manually craft a string with a header claiming 2 sections but only 1 payload
    const header: GppHeader = {
      version: 1,
      sectionIds: [7, 8],
      applicableSections: [],
    };
    const headerStr = encodeGppHeader(header);
    const sectionStr = encodeSectionCore(US_NATIONAL, createDefaultSectionData(US_NATIONAL));
    const gppStr = `${headerStr}~${sectionStr}`;
    // Missing section 8 payload

    expect(() => decodeGppString(gppStr)).toThrow('Missing payload for GPP section 8');
  });
});

// ── Convenience helpers ──────────────────────────────────────────────

describe('createDefaultSectionData', () => {
  it('creates default data with correct Version for each section', () => {
    for (const def of SECTION_REGISTRY.values()) {
      const data = createDefaultSectionData(def);
      expect(data.Version).toBe(def.version);
    }
  });

  it('creates correct array sizes for US National', () => {
    const data = createDefaultSectionData(US_NATIONAL);
    expect((data.SensitiveDataProcessing as number[]).length).toBe(12);
    expect((data.KnownChildSensitiveDataConsents as number[]).length).toBe(2);
  });

  it('creates correct array sizes for US California', () => {
    const data = createDefaultSectionData(US_CALIFORNIA);
    expect((data.SensitiveDataProcessing as number[]).length).toBe(9);
    expect((data.KnownChildSensitiveDataConsents as number[]).length).toBe(2);
  });

  it('creates correct array sizes for US Connecticut', () => {
    const data = createDefaultSectionData(US_CONNECTICUT);
    expect((data.SensitiveDataProcessing as number[]).length).toBe(8);
    expect((data.KnownChildSensitiveDataConsents as number[]).length).toBe(3);
  });

  it('uses scalar for single-count fields', () => {
    const data = createDefaultSectionData(US_VIRGINIA);
    expect(typeof data.KnownChildSensitiveDataConsents).toBe('number');
  });
});

describe('getSectionByPrefix', () => {
  it('finds US National by prefix', () => {
    expect(getSectionByPrefix('usnat')).toBe(US_NATIONAL);
  });

  it('finds US California by prefix', () => {
    expect(getSectionByPrefix('usca')).toBe(US_CALIFORNIA);
  });

  it('finds US Virginia by prefix', () => {
    expect(getSectionByPrefix('usva')).toBe(US_VIRGINIA);
  });

  it('finds US Colorado by prefix', () => {
    expect(getSectionByPrefix('usco')).toBe(US_COLORADO);
  });

  it('finds US Connecticut by prefix', () => {
    expect(getSectionByPrefix('usct')).toBe(US_CONNECTICUT);
  });

  it('finds US Florida by prefix', () => {
    expect(getSectionByPrefix('usfl')).toBe(US_FLORIDA);
  });

  it('returns undefined for unknown prefix', () => {
    expect(getSectionByPrefix('unknown')).toBeUndefined();
  });
});

describe('registerSection', () => {
  it('registers a custom section definition', () => {
    const customDef: SectionDef = {
      id: 99,
      apiPrefix: 'custom',
      version: 1,
      coreFields: [
        { name: 'Version', bits: 6, count: 1 },
        { name: 'OptOut', bits: 2, count: 1 },
      ],
      hasGpcSubsection: false,
    };

    registerSection(customDef);
    expect(SECTION_REGISTRY.get(99)).toBe(customDef);
    expect(getSectionByPrefix('custom')).toBe(customDef);

    // Encode and decode using the custom section
    const data: SectionData = { Version: 1, OptOut: 2 };
    const encoded = encodeSectionCore(customDef, data);
    const decoded = decodeSectionCore(customDef, encoded);
    expect(decoded.Version).toBe(1);
    expect(decoded.OptOut).toBe(2);

    // Clean up
    SECTION_REGISTRY.delete(99);
  });
});

// ── Section registry completeness ────────────────────────────────────

describe('section registry', () => {
  it('contains all required sections', () => {
    expect(SECTION_REGISTRY.has(7)).toBe(true);  // US National
    expect(SECTION_REGISTRY.has(8)).toBe(true);  // US California
    expect(SECTION_REGISTRY.has(9)).toBe(true);  // US Virginia
    expect(SECTION_REGISTRY.has(10)).toBe(true); // US Colorado
    expect(SECTION_REGISTRY.has(11)).toBe(true); // US Connecticut
    expect(SECTION_REGISTRY.has(14)).toBe(true); // US Florida
  });

  it('each section has a unique apiPrefix', () => {
    const prefixes = new Set<string>();
    for (const def of SECTION_REGISTRY.values()) {
      expect(prefixes.has(def.apiPrefix)).toBe(false);
      prefixes.add(def.apiPrefix);
    }
  });

  it('each section has a unique id', () => {
    const ids = new Set<number>();
    for (const def of SECTION_REGISTRY.values()) {
      expect(ids.has(def.id)).toBe(false);
      ids.add(def.id);
    }
  });
});

// ── GppFieldValue constants ──────────────────────────────────────────

describe('GppFieldValue constants', () => {
  it('has correct values', () => {
    expect(GppFieldValue.NOT_APPLICABLE).toBe(0);
    expect(GppFieldValue.YES).toBe(1);
    expect(GppFieldValue.NO).toBe(2);
  });
});

// ── Edge cases ───────────────────────────────────────────────────────

describe('edge cases', () => {
  it('handles all-maximum field values for US National', () => {
    const data: SectionData = {
      Version: 63, // max for 6 bits
      SharingNotice: 3, // max for 2 bits
      SaleOptOutNotice: 3,
      SharingOptOutNotice: 3,
      TargetedAdvertisingOptOutNotice: 3,
      SensitiveDataProcessingOptOutNotice: 3,
      SensitiveDataLimitUseNotice: 3,
      SaleOptOut: 3,
      SharingOptOut: 3,
      TargetedAdvertisingOptOut: 3,
      SensitiveDataProcessing: new Array(12).fill(3),
      KnownChildSensitiveDataConsents: [3, 3],
      PersonalDataConsents: 3,
      MspaCoveredTransaction: 3,
      MspaOptOutOptionMode: 3,
      MspaServiceProviderMode: 3,
    };
    const encoded = encodeSectionCore(US_NATIONAL, data);
    const decoded = decodeSectionCore(US_NATIONAL, encoded);
    expect(decoded.Version).toBe(63);
    expect(decoded.SharingNotice).toBe(3);
    expect(decoded.SensitiveDataProcessing).toEqual(new Array(12).fill(3));
  });

  it('handles a GPP string with all 6 registered sections', () => {
    const allSectionIds = [7, 8, 9, 10, 11, 14];
    const sections = new Map<number, { data: SectionData; gpcSubsection?: { gpc: boolean } }>();

    for (const id of allSectionIds) {
      const def = SECTION_REGISTRY.get(id)!;
      sections.set(id, {
        data: createDefaultSectionData(def),
        gpcSubsection: def.hasGpcSubsection ? { gpc: true } : undefined,
      });
    }

    const gpp: GppString = {
      header: {
        version: 1,
        sectionIds: allSectionIds,
        applicableSections: allSectionIds,
      },
      sections,
    };

    const encoded = encodeGppString(gpp);
    const decoded = decodeGppString(encoded);

    expect(decoded.header.sectionIds).toEqual(allSectionIds);
    expect(decoded.sections.size).toBe(6);

    for (const id of allSectionIds) {
      expect(decoded.sections.has(id)).toBe(true);
      const def = SECTION_REGISTRY.get(id)!;
      if (def.hasGpcSubsection) {
        expect(decoded.sections.get(id)!.gpcSubsection?.gpc).toBe(true);
      }
    }
  });
});
