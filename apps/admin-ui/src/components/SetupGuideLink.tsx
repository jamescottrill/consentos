import { getSetupGuide } from '../extensions/registry';

/** The registered setup guide, as a link. Renders nothing without one. */
export default function SetupGuideLink({ className }: { className?: string }) {
  const guide = getSetupGuide();
  if (!guide) return null;
  return (
    <a
      href={guide.href}
      target="_blank"
      rel="noopener noreferrer"
      className={`text-xs font-medium text-copper hover:underline ${className ?? ''}`}
    >
      {guide.label}
    </a>
  );
}
