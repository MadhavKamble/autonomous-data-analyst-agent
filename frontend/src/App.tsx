import { Chat } from "./components/Chat";

function App(): React.JSX.Element {
  return (
    <div className="app">
      <header className="app-header">
        <h1>Autonomous Data-Analyst Agent</h1>
        <p className="app-subtitle">
          Ask a question about ride-sharing data. Every answer includes a full reasoning trace.
        </p>
      </header>
      <Chat />
    </div>
  );
}

export default App;
