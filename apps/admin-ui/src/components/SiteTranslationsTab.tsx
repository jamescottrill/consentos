import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import type { FormEvent } from 'react';

import {
  createTranslation,
  deleteTranslation,
  listTranslations,
  updateTranslation,
} from '../api/translations';
import type { Translation } from '../types/api';
import { Alert } from './ui/alert';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';
import { EmptyState } from './ui/empty-state';
import { FormField } from './ui/form-field';
import { Input } from './ui/input';
import { LoadingState } from './ui/loading-state';
import { Modal } from './ui/modal';
import { Select } from './ui/select';
import { Textarea } from './ui/textarea';
import { refusedReason } from '../api/revisions';

/** The translation keys that the banner script expects. */
const TRANSLATION_KEYS = [
  { key: 'title', label: 'Banner title', placeholder: 'We use cookies' },
  {
    key: 'description',
    label: 'Banner description',
    placeholder: 'We use cookies and similar technologies...',
    multiline: true,
  },
  { key: 'acceptAll', label: 'Accept all button', placeholder: 'Accept all' },
  { key: 'rejectAll', label: 'Reject all button', placeholder: 'Reject all' },
  {
    key: 'managePreferences',
    label: 'Manage preferences button',
    placeholder: 'Manage preferences',
  },
  { key: 'savePreferences', label: 'Save preferences button', placeholder: 'Save preferences' },
  { key: 'privacyPolicyLink', label: 'Privacy policy link text', placeholder: 'Privacy Policy' },
  { key: 'closeLabel', label: 'Close button label', placeholder: 'Close' },
  { key: 'categoryNecessary', label: 'Necessary category', placeholder: 'Necessary' },
  {
    key: 'categoryNecessaryDesc',
    label: 'Necessary description',
    placeholder: 'Essential for the website to function.',
  },
  { key: 'categoryFunctional', label: 'Functional category', placeholder: 'Functional' },
  {
    key: 'categoryFunctionalDesc',
    label: 'Functional description',
    placeholder: 'Enable enhanced functionality.',
  },
  { key: 'categoryAnalytics', label: 'Analytics category', placeholder: 'Analytics' },
  {
    key: 'categoryAnalyticsDesc',
    label: 'Analytics description',
    placeholder: 'Help us understand how visitors interact.',
  },
  { key: 'categoryMarketing', label: 'Marketing category', placeholder: 'Marketing' },
  {
    key: 'categoryMarketingDesc',
    label: 'Marketing description',
    placeholder: 'Used to deliver personalised advertisements.',
  },
  {
    key: 'categoryPersonalisation',
    label: 'Personalisation category',
    placeholder: 'Personalisation',
  },
  {
    key: 'categoryPersonalisationDesc',
    label: 'Personalisation description',
    placeholder: 'Enable content personalisation.',
  },
  {
    key: 'cookieCount',
    label: 'Cookie count text',
    placeholder: '{{count}} cookies used on this site',
  },
];

const COMMON_LOCALES = [
  { code: 'en', name: 'English' },
  { code: 'fr', name: 'French' },
  { code: 'de', name: 'German' },
  { code: 'es', name: 'Spanish' },
  { code: 'it', name: 'Italian' },
  { code: 'nl', name: 'Dutch' },
  { code: 'pt', name: 'Portuguese' },
  { code: 'pl', name: 'Polish' },
  { code: 'sv', name: 'Swedish' },
  { code: 'da', name: 'Danish' },
  { code: 'fi', name: 'Finnish' },
  { code: 'no', name: 'Norwegian' },
  { code: 'cs', name: 'Czech' },
  { code: 'ro', name: 'Romanian' },
  { code: 'hu', name: 'Hungarian' },
  { code: 'bg', name: 'Bulgarian' },
  { code: 'hr', name: 'Croatian' },
  { code: 'sk', name: 'Slovak' },
  { code: 'sl', name: 'Slovenian' },
  { code: 'el', name: 'Greek' },
  { code: 'ja', name: 'Japanese' },
  { code: 'ko', name: 'Korean' },
  { code: 'zh', name: 'Chinese' },
  { code: 'ar', name: 'Arabic' },
];

interface Props {
  siteId: string;
}

export default function SiteTranslationsTab({ siteId }: Props) {
  const queryClient = useQueryClient();
  const [selectedLocale, setSelectedLocale] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const { data: translations, isLoading } = useQuery({
    queryKey: ['sites', siteId, 'translations'],
    queryFn: () => listTranslations(siteId),
  });

  const deleteMutation = useMutation({
    mutationFn: (locale: string) => deleteTranslation(siteId, locale),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sites', siteId, 'translations'] });
      setSelectedLocale(null);
    },
  });

  if (isLoading) {
    return <LoadingState />;
  }

  const existing = translations ?? [];
  const selected = existing.find((t) => t.locale === selectedLocale);

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="p-6">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="font-heading text-sm font-semibold text-foreground">Translations</h3>
              <p className="mt-0.5 text-xs text-text-secondary">
                Manage banner text for different languages. English is the default fallback.
              </p>
            </div>
            <Button onClick={() => setShowCreate(true)}>
              Add language
            </Button>
          </div>

          {existing.length === 0 ? (
            <EmptyState message="No translations yet. The banner will use English defaults." />
          ) : (
            <div className="flex flex-wrap gap-2">
              {existing.map((t) => (
                <button
                  key={t.locale}
                  onClick={() => setSelectedLocale(t.locale)}
                  className={`rounded-lg border px-4 py-2 text-sm font-medium transition ${
                    selectedLocale === t.locale
                      ? 'border-copper bg-copper/10 text-copper'
                      : 'border-border text-text-secondary hover:bg-mist'
                  }`}
                >
                  {localeName(t.locale)}
                  <span className="ml-1.5 text-xs text-text-tertiary">{t.locale}</span>
                </button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {selected && (
        <TranslationEditor
          siteId={siteId}
          translation={selected}
          onDelete={() => {
            if (confirm(`Delete ${localeName(selected.locale)} translation?`)) {
              deleteMutation.mutate(selected.locale);
            }
          }}
        />
      )}

      <CreateTranslationModal
        open={showCreate}
        siteId={siteId}
        existingLocales={existing.map((t) => t.locale)}
        onClose={() => setShowCreate(false)}
        onCreated={(locale) => {
          setShowCreate(false);
          setSelectedLocale(locale);
        }}
      />
    </div>
  );
}

/* ── Translation editor ──────────────────────────────────────────────── */

function TranslationEditor({
  siteId,
  translation,
  onDelete,
}: {
  siteId: string;
  translation: Translation;
  onDelete: () => void;
}) {
  const queryClient = useQueryClient();
  const [strings, setStrings] = useState<Record<string, string>>(translation.strings);
  const [saved, setSaved] = useState(false);

  // Reset state when switching locales
  const [currentLocale, setCurrentLocale] = useState(translation.locale);
  if (translation.locale !== currentLocale) {
    setStrings(translation.strings);
    setCurrentLocale(translation.locale);
    setSaved(false);
  }

  const mutation = useMutation({
    mutationFn: (body: { strings: Record<string, string> }) =>
      updateTranslation(siteId, translation.locale, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sites', siteId, 'translations'] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    mutation.mutate({ strings });
  };

  const filledCount = TRANSLATION_KEYS.filter((k) => strings[k.key]?.trim()).length;

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <Card>
        <CardContent className="p-6">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="font-heading text-sm font-semibold text-foreground">
                {localeName(translation.locale)}{' '}
                <span className="font-normal text-text-tertiary">({translation.locale})</span>
              </h3>
              <p className="mt-0.5 text-xs text-text-secondary">
                {filledCount}/{TRANSLATION_KEYS.length} strings translated. Empty strings fall back
                to English.
              </p>
            </div>
            <button
              type="button"
              onClick={onDelete}
              className="text-xs text-status-error-fg hover:underline"
            >
              Delete language
            </button>
          </div>

          <div className="space-y-4">
            {TRANSLATION_KEYS.map(({ key, label, placeholder, multiline }) => (
              <div key={key}>
                <label className="mb-1 block text-xs font-medium text-text-secondary">
                  {label}
                  <span className="ml-1 font-mono text-text-tertiary">{key}</span>
                </label>
                {multiline ? (
                  <Textarea
                    value={strings[key] ?? ''}
                    onChange={(e) => setStrings({ ...strings, [key]: e.target.value })}
                    placeholder={placeholder}
                    rows={3}
                  />
                ) : (
                  <Input
                    type="text"
                    value={strings[key] ?? ''}
                    onChange={(e) => setStrings({ ...strings, [key]: e.target.value })}
                    placeholder={placeholder}
                  />
                )}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="flex items-center gap-3">
        <Button
          type="submit"
          disabled={mutation.isPending}
        >
          {mutation.isPending ? 'Saving...' : 'Save translation'}
        </Button>
        {saved && <span className="text-sm text-status-success-fg">Saved successfully</span>}
        {mutation.isError && (
          <span className="text-sm text-status-error-fg">
            {refusedReason(mutation.error) ?? 'Failed to save. Please try again.'}
          </span>
        )}
      </div>
    </form>
  );
}

/* ── Create translation modal ────────────────────────────────────────── */

function CreateTranslationModal({
  open,
  siteId,
  existingLocales,
  onClose,
  onCreated,
}: {
  open: boolean;
  siteId: string;
  existingLocales: string[];
  onClose: () => void;
  onCreated: (locale: string) => void;
}) {
  const queryClient = useQueryClient();
  const [locale, setLocale] = useState('');
  const [error, setError] = useState('');

  const availableLocales = COMMON_LOCALES.filter((l) => !existingLocales.includes(l.code));

  const mutation = useMutation({
    mutationFn: () => createTranslation(siteId, { locale, strings: {} }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sites', siteId, 'translations'] });
      onCreated(locale);
    },
    onError: (err) => {
      setError(refusedReason(err) ?? 'Failed to create translation. The locale may already exist.');
    },
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!locale) return;
    setError('');
    mutation.mutate();
  };

  return (
    <Modal open={open} onClose={onClose} title="Add language">
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && (
          <Alert variant="error">{error}</Alert>
        )}
        <FormField label="Language" htmlFor="locale">
          <Select
            id="locale"
            required
            value={locale}
            onChange={(e) => setLocale(e.target.value)}
          >
            <option value="">Select a language...</option>
            {availableLocales.map((l) => (
              <option key={l.code} value={l.code}>
                {l.name} ({l.code})
              </option>
            ))}
          </Select>
        </FormField>
        <div className="flex justify-end gap-3 pt-2">
          <Button
            type="button"
            variant="ghost"
            onClick={onClose}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            disabled={mutation.isPending || !locale}
          >
            {mutation.isPending ? 'Creating...' : 'Add language'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/* ── Helpers ──────────────────────────────────────────────────────────── */

function localeName(code: string): string {
  const match = COMMON_LOCALES.find((l) => l.code === code);
  return match?.name ?? code.toUpperCase();
}
