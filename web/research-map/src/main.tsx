import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { loadModel } from "./data";
import "./styles.css";

const root = createRoot(document.getElementById("root")!);

loadModel()
  .then((model) =>
    root.render(
      <StrictMode>
        <App model={model} />
      </StrictMode>,
    ),
  )
  .catch((err: Error) => {
    root.render(
      <p style={{ padding: 24, fontFamily: "system-ui", color: "#d92b1f" }}>
        {err.message}
      </p>,
    );
  });
