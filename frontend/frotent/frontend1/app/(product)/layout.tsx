import { ProductShell } from "../../components/layout/product-shell";

export default function ProductLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <ProductShell>{children}</ProductShell>;
}
