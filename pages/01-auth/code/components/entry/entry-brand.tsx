import Link from "next/link";

export function EntryBrand({ href = "/auth" }: { href?: string }) {
  return (
    <Link href={href} className="xn-entry-brand" aria-label="向新首页">
      <span className="xn-brand-mark" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span>
        <strong>向新</strong>
        <small>AI职业成长伙伴</small>
      </span>
    </Link>
  );
}
