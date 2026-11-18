import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { LaundryStation, MakeupStation } from "./laundry";
import { Reception } from "./reception";
import { Simulator } from "./simulator";
import { BigScreenTest } from "./wire";
import "./styles.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("Missing root");
}

const path = window.location.pathname.replace(/\/+$/, "") || "/";

function stayToken(current: string): string {
  if (current.startsWith("/room/")) return decodeURIComponent(current.slice("/room/".length));
  if (current.startsWith("/simulator/")) return decodeURIComponent(current.slice("/simulator/".length));
  return "";
}

const stay = stayToken(path);
const guestApp = path === "/room" || path === "/simulator" || stay !== "";
if (
  !guestApp &&
  path !== "/laundry" &&
  path !== "/makeup" &&
  path !== "/reception" &&
  path !== "/test"
) {
  window.location.replace("/room");
}

function page() {
  if (path === "/laundry") return <LaundryStation />;
  if (path === "/makeup") return <MakeupStation />;
  if (path === "/reception") return <Reception />;
  if (path === "/test") return <BigScreenTest />;
  return <Simulator token={stay} />;
}

createRoot(root).render(<StrictMode>{page()}</StrictMode>);
