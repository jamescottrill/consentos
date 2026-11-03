import { describe, expect, it } from 'vitest';

import { publishErrorMessage, refusedReason } from '../api/revisions';

const failure = (status: number, data: object) => ({ response: { status, data } });

describe('publishErrorMessage', () => {
  it('explains a role refusal', () => {
    expect(publishErrorMessage(failure(403, { detail: 'Role' }), 'x')).toMatch(/owners and admins/);
  });

  it("shows a refused action's own reason", () => {
    const error = failure(403, { detail: 'Not allowed here', code: 'action_refused' });
    expect(publishErrorMessage(error, 'x')).toBe('Not allowed here');
  });
});

describe('refusedReason', () => {
  it('is only set for refused actions', () => {
    expect(refusedReason(failure(403, { detail: 'No', code: 'action_refused' }))).toBe('No');
    expect(refusedReason(failure(409, { detail: 'Taken' }))).toBeUndefined();
  });
});
