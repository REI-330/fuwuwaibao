export function PageHeading({ title, subtitle, children }: { title: string; subtitle: string; children?: React.ReactNode }) {
  return <div className="xn-page-heading"><div><h1>{title}</h1><p>{subtitle}</p></div>{children}</div>;
}
