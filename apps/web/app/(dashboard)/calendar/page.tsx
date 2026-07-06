import { redirect } from "next/navigation";

export default function CalendarRedirectPage() {
  redirect("/meetings?tab=upcoming");
}
