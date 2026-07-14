import { useMsal } from '@azure/msal-react';
import { Button, Title1, Body1, makeStyles, tokens } from '@fluentui/react-components';
import { SignOutRegular } from '@fluentui/react-icons';
import { BuiltWithBadge } from './core/BuiltWithBadge';
import { loginRequest } from '../config/authConfig';

const useStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    height: '100vh',
    width: '100%',
    background: tokens.colorNeutralBackground1,
    position: 'relative',
  },
  card: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '32px',
    padding: '48px 40px',
    maxWidth: '400px',
    width: '100%',
    textAlign: 'center',
  },
  iconContainer: {
    width: '72px',
    height: '72px',
    borderRadius: tokens.borderRadiusMedium,
    background: tokens.colorBrandBackground2,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: tokens.colorBrandForeground1,
  },
  icon: {
    width: '40px',
    height: '40px',
  },
  textGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },
  description: {
    color: tokens.colorNeutralForeground2,
    lineHeight: 1.5,
  },
  signInButton: {
    width: '100%',
    height: '44px',
    fontSize: '15px',
    fontWeight: 600,
  },
  badgeWrapper: {
    position: 'absolute',
    bottom: '24px',
  },
});

export function LoginPage() {
  const styles = useStyles();
  const { instance } = useMsal();

  const handleSignIn = () => {
    instance.loginRedirect(loginRequest);
  };

  return (
    <div className={styles.root}>
      <div className={styles.card}>
        <div className={styles.iconContainer}>
          <svg
            className={styles.icon}
            fill="currentColor"
            viewBox="0 0 20 20"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path d="M18.3515 6.60197C18.3515 6.34706 18.1451 6.15283 17.9024 6.15283H15.778C14.2849 6.15283 13.071 7.36673 13.071 8.85983V13.3513H15.6445C17.1376 13.3513 18.3515 12.1374 18.3515 10.6443V6.60197Z" />
            <path
              clipRule="evenodd"
              d="M12.3185 1.00586L12.2457 15.3906C12.2457 17.3814 10.6312 18.9959 8.64039 18.9959H2.09747C1.78186 18.9959 1.5755 18.6924 1.67261 18.401L6.91666 3.42152C7.42649 1.97698 8.78606 1.00586 10.3156 1.00586H12.3185Z"
              fillRule="evenodd"
            />
          </svg>
        </div>

        <div className={styles.textGroup}>
          <Title1 as="h1">Azure AI Agent</Title1>
          <Body1 className={styles.description}>
            Sign in with your Microsoft account to start chatting with your AI agent.
          </Body1>
        </div>

        <Button
          className={styles.signInButton}
          appearance="primary"
          icon={<SignOutRegular />}
          onClick={handleSignIn}
        >
          Sign in with Microsoft
        </Button>
      </div>

      <div className={styles.badgeWrapper}>
        <BuiltWithBadge />
      </div>
    </div>
  );
}
