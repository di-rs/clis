import { mount } from "svelte";
import "@xterm/xterm/css/xterm.css";
import "./app.css";
import App from "./App.svelte";

const target = document.getElementById("app");
if (!target) throw new Error("The playground mount element is missing");
mount(App, { target });
