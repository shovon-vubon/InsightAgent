import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/features/auth/login-form";
import { ApiError } from "@/services/api-client";

const replace = vi.fn();
const login = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

vi.mock("@/features/auth/auth-context", () => ({
  useAuth: () => ({ user: null, isLoading: false, login, logout: vi.fn() }),
}));

describe("LoginForm", () => {
  beforeEach(() => {
    replace.mockReset();
    login.mockReset();
  });

  it("renders labelled, correctly typed fields", () => {
    render(<LoginForm />);

    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("submits the credentials and navigates to the workspace", async () => {
    login.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<LoginForm />);

    await user.type(screen.getByLabelText("Email"), "analyst@example.com");
    await user.type(screen.getByLabelText("Password"), "a-long-enough-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(login).toHaveBeenCalledWith({
        email: "analyst@example.com",
        password: "a-long-enough-password",
      });
    });
    expect(replace).toHaveBeenCalledWith("/research");
  });

  it("shows a non-enumerating message when credentials are rejected", async () => {
    login.mockRejectedValue(new ApiError(401, null, "Unauthorized"));
    const user = userEvent.setup();
    render(<LoginForm />);

    await user.type(screen.getByLabelText("Email"), "analyst@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password-here");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Incorrect email or password.");
    expect(replace).not.toHaveBeenCalled();
  });

  it("distinguishes an outage from a rejected credential", async () => {
    login.mockRejectedValue(new ApiError(503, null, "Service Unavailable"));
    const user = userEvent.setup();
    render(<LoginForm />);

    await user.type(screen.getByLabelText("Email"), "analyst@example.com");
    await user.type(screen.getByLabelText("Password"), "a-long-enough-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");
  });
});
