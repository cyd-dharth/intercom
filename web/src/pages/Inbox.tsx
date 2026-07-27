import { useAuth } from "../auth";

export default function Inbox() {
  const { currentWorkspace } = useAuth();

  return (
    <div className="h-full flex">
      <div className="w-80 border-r bg-white flex flex-col">
        <div className="p-4 border-b font-medium">Inbox</div>
        <div className="flex-1 flex items-center justify-center text-sm text-gray-400 p-6 text-center">
          No conversations yet. Once a visitor messages you through the widget, or an email comes in, it will
          show up here.
        </div>
      </div>
      <div className="flex-1 flex items-center justify-center text-gray-400">
        {currentWorkspace ? (
          <p>Select a conversation to get started.</p>
        ) : (
          <p>Loading workspace...</p>
        )}
      </div>
    </div>
  );
}
