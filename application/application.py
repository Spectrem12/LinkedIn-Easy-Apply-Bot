import enum
import logging
import random
import time

import win32com.client as comctl
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from transitions import Machine

#logging.basicConfig(level=logging.DEBUG)
# Set transitions' log level to INFO; DEBUG messages will be omitted
logging.getLogger('transitions').setLevel(logging.INFO)

wsh = comctl.Dispatch("WScript.Shell")

class loggingAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return '[%s] %s' % (self.extra['state'], msg), kwargs

class Application(Machine):
    """
    This class is meant to handle the application popup that only occurs when a company has an EASY APPLY button
    It is intended to be a self driven machine that only needs to be kicked off by the easy apply bot.
    :arg
    """
    class States(enum.Enum):
        NONE = 0
        INFO = 1        #Screen where basic applicant info is entered (manually or automatically)
        UPLOAD = 2      #Screen where user uploads cover letter and/or resume
        PHOTO = 3
        QUESTIONS1 = 4   #Screen where user is asked additional questions. EX: Do you have a Bachelor's degree
        QUESTIONS2 = 5  # Sometimes there are multiple pages of questions.
        REVIEW = 6      #Screen where user verifies all application information is correct
        SUBMITTED = 7       #Confirmation screen that declares if application was submitted correctly or not
        ERROR = 8       #State of any screen where an error has occurred
        SUSPENDED = 9   #State if in infinite error loop and cannot exit

    # Order matters on the transitions. As the higher up on the list the transition is for that state, the earlier it is in the application popup
    # Order and transition must be in the order below since error is expected, while suspended is an all out failure
    transitions = [
        {'trigger': 'next', 'source': States.INFO, 'dest': States.UPLOAD, 'conditions':['check_if_upload','go_to_next'], 'unless': 'check_for_error', 'after':'upload'},
        {'trigger': 'next', 'source': States.INFO, 'dest': States.QUESTIONS1, 'conditions': ['check_if_questions','go_to_next'],'unless': 'check_for_error', 'after': 'answer_questions'},
        {'trigger': 'next', 'source': States.INFO, 'dest': States.REVIEW, 'conditions': 'go_to_review','unless': 'check_for_error',},
        {'trigger': 'next', 'source': States.INFO, 'dest': States.SUBMITTED, 'conditions': 'submit_app', 'unless': 'check_for_error'},
        {'trigger': 'next', 'source': States.INFO, 'dest': States.ERROR, 'conditions': 'check_for_error',
         'after': 'determine_error'},


        {'trigger': 'next', 'source': States.UPLOAD, 'dest': States.QUESTIONS1, 'conditions':['check_if_questions','go_to_next'], 'unless': 'check_for_error', 'after':'answer_questions'},
        {'trigger': 'next', 'source': States.UPLOAD, 'dest': States.REVIEW, 'conditions': 'go_to_review','unless': 'check_for_error',},
        {'trigger': 'next', 'source': States.UPLOAD, 'dest': States.ERROR, 'conditions': 'check_for_error',
         'after': 'determine_error'},


        {'trigger': 'next', 'source': States.QUESTIONS1, 'dest': States.REVIEW, 'conditions':'go_to_review', 'unless': 'check_for_error'},
        {'trigger': 'next', 'source': States.QUESTIONS1, 'dest': States.QUESTIONS2, 'conditions': 'go_to_next','unless': 'check_for_error', 'after': 'answer_questions'},
        {'trigger': 'next', 'source': States.QUESTIONS1, 'dest': States.ERROR, 'conditions': 'check_for_error',
         'after': 'determine_error'},


        {'trigger': 'next', 'source': States.QUESTIONS2, 'dest': States.REVIEW, 'conditions': 'go_to_review','unless': 'check_for_error'},
        {'trigger': 'next', 'source': States.QUESTIONS2, 'dest': States.ERROR, 'conditions': 'check_for_error',
         'after': 'determine_error'},


        {'trigger': 'next', 'source': States.REVIEW, 'dest': States.SUBMITTED, 'conditions':'submit_app', 'unless': 'check_for_error'},
        {'trigger': 'next', 'source': States.REVIEW, 'dest': States.ERROR, 'conditions': 'check_for_error',
         'after': 'determine_error'},

        #if we are not able to get to one of the states we need to go to, then there might be an error
        #if there is an issue with going to the error state, then we give up and suspend so the application exits.
        {'trigger': 'next', 'source': '*', 'dest': States.ERROR, 'conditions': 'check_for_error', 'after':'determine_error'},
        {'trigger': 'next', 'source': '*', 'dest': States.SUSPENDED, 'after': 'shout_suspension'},

        #since we rely upon a while loop calling 'next' for transitions, we still need to have 2 specific triggers so we can jump to these states when needed.
        {'trigger': 'suspend', 'source': '*', 'dest': States.SUSPENDED, 'after': 'shout_suspension'},
        {'trigger': 'find_fail', 'source': '*', 'dest': States.ERROR, 'conditions': 'check_for_error', 'after': 'determine_error'},

        {'trigger': 'to_INFO', 'source': States.ERROR, 'dest': States.INFO},
        {'trigger': 'to_QUESTIONS1', 'source': States.ERROR, 'dest': States.QUESTIONS1, 'after': 'answer_questions'},
        {'trigger': 'to_QUESTIONS2', 'source': States.ERROR, 'dest': States.QUESTIONS2, 'after': 'answer_questions'},
        {'trigger': 'to_REVIEW', 'source': States.ERROR, 'dest': States.REVIEW},


    ]



    def __init__(self, name, browser, uploads = {}):
        #All states will call app_sleep on state change.
        Machine.__init__(self, states=self.States, initial=self.States.INFO,transitions=self.transitions, after_state_change='app_sleep')
        self.name = name
        self.browser = browser
        self.uploads = uploads
        self.wait = WebDriverWait(self.browser, 30)

        log = logging.getLogger(__name__)
        self.sl = log#loggingAdapter(log, {'state': self.__getattr__()})

        #TODO These locators are not future proof. These labels could easily change.
        # Ideally we would search for contained text;
        # was unable to get it to work using XPATH and searching for contained text
        #TODO Should change these locators to namedtuples for easier readibility
        self.upload_locator = (By.CSS_SELECTOR, "label[aria-label='DOC, DOCX, PDF formats only (2 MB).']")
        self.cover_letter = (By.CSS_SELECTOR, "input[name='file']")

        self.next_locator = (By.CSS_SELECTOR, "button[aria-label='Continue to next step']")
        self.review_locator = (By.CSS_SELECTOR, "button[aria-label='Review your application']")
        self.submit_locator = (By.CSS_SELECTOR, "button[aria-label='Submit application']")
        self.submit_application_locator = (By.CSS_SELECTOR, "button[aria-label='Submit application']")

        self.button_locators = [self.next_locator, self.review_locator, self.submit_locator, self.submit_application_locator]

        self.error_locator = (By.CSS_SELECTOR, "p[data-test-form-element-error-message='true']")
        self.error_locator_hidden = (By.CSS_SELECTOR, "p[class='fb-form-element__error-text t-12 visually-hidden']")
        self.error_locator_not_hidden = (By.CSS_SELECTOR, "p[class='fb-form-element__error-text t-12']")

        self.question_locator = (By.XPATH, ".//div[@class='jobs-easy-apply-form-section__grouping']")
        self.yes_locator = (By.XPATH, ".//input[@value='Yes']")
        self.no_locator = (By.XPATH, ".//input[@value='No']")
        self.textInput_locator = (By.XPATH, ".//input[@type='text']")



       # self.machine = Machine(model=self, states=States )

    def is_present(self, button_locator):
        return len(self.browser.find_elements(button_locator[0], button_locator[1])) > 0

    def answer_questions(self):
        """
        This function is used in any of the question states to answer custom questions requested by the company being applied to.
        This function is to be used by all questions states, since there currently isnt a way to determine which questions
        are in which questions state.
        :arg
        """
        # TODO these questions will need to be logged so that way, individuals can look through the logs and add them at the end of an application run.
        # Required question expects an answer. Search through possible questions/answer combos
        try:
            self.sl.info("Attempting to answer questions")
            if self.is_present(self.question_locator):# and attemptQuestions:
                questionSections = self.browser.find_elements(self.question_locator[0], self.question_locator[1])
                for questionElement in questionSections:
                    try:
                        self.sl.info("Found test element %s", questionElement)
                        text = questionElement.text
                        self.sl.warning("Question Text: %s", text)

                        # assuming this question is asking if I am authorized to work in the US
                        if ("Are you" in text and "authorized" in text) or (
                                "Have You" in text and "education" in text):
                            # Be sure to find the child element of the current test question section
                            yesRadio = questionElement.find_element(By.XPATH, self.yes_locator[1])
                            time.sleep(1)
                            self.sl.info("Attempting to click the radio button for %s", self.yes_locator)
                            self.browser.execute_script("arguments[0].click()", yesRadio)
                            self.sl.info("Clicked the radio button %s", self.yes_locator)

                        # assuming this question is asking if I require sponsorship
                        elif "require" in text and "sponsorship" in text:
                            noRadio = questionElement.find_element(By.XPATH, self.no_locator[1])
                            time.sleep(1)
                            self.sl.info("Attempting to click the radio button for %s", self.no_locator)
                            self.browser.execute_script("arguments[0].click()", noRadio)
                            self.sl.info("Clicked the radio button %s", self.no_locator)

                        # assuming this question is asking if I have a Bachelor's degree
                        elif (("You have" in text) or ("Have you" in text)) and "Bachelor's" in text:
                            yesRadio = questionElement.find_element(By.XPATH, self.yes_locator[1])
                            time.sleep(1)
                            self.sl.info("Attempting to click the radio button for %s", self.yes_locator)
                            self.browser.execute_script("arguments[0].click()", yesRadio)
                            self.sl.info("Clicked the radio button %s", self.yes_locator)

                        # assuming this question is asking if I have a Master's degree
                        elif (("You have" in text) or ("Have you" in text)) and "Master's" in text:
                            yesRadio = questionElement.find_element(By.XPATH, self.yes_locator[1])
                            time.sleep(1)
                            self.sl.info("Attempting to click the radio button for %s", self.yes_locator)
                            self.browser.execute_script("arguments[0].click()", yesRadio)
                            self.sl.info("Clicked the radio button %s", self.yes_locator)

                        # TODO Issue where if there are multiple lines that ask for number of years experience then years experience will be written twice
                        # TODO Need to add a configuration file with all the answer for these questions versus having them hardcoded.
                        # Some questions are asking how many years of experience you have in a specific skill
                        # Automatically put the number of years that I have worked.
                        elif "How many years" in text and "experience" in text:
                            textField = questionElement.find_element(By.XPATH, self.textInput_locator[1])
                            textField.clear()
                            textFieldValue = textField.get_attribute("value")
                            if not textFieldValue:
                                time.sleep(1)
                                self.sl.info("Attempting to click the text field for %s", self.textInput_locator)
                                self.browser.execute_script("arguments[0].click()", textField)
                                self.sl.info("Clicked the text field %s", self.textInput_locator)
                                time.sleep(1)
                                self.sl.info("Attempting to send keys to the text field %s", self.textInput_locator)
                                textField.send_keys("10")
                                self.sl.info("Sent keys to the text field %s", self.textInput_locator)

                            textFieldValue = textField.get_attribute("value")
                            self.sl.info("Current text field input value is %s", textFieldValue)

                        # This should be updated to match the language you speak.
                        elif "Do you" in text and "speak" in text:
                            if "English" in text:
                                yesRadio = questionElement.find_element(By.XPATH, self.yes_locator[1])
                                time.sleep(1)
                                self.sl.info("Attempting to click the radio button for %s", self.yes_locator)
                                self.browser.execute_script("arguments[0].click()", yesRadio)
                                self.sl.info("Clicked the radio button %s", self.yes_locator)
                            # if not english then say no.
                            else:
                                noRadio = questionElement.find_element(By.XPATH, self.no_locator[1])
                                time.sleep(1)
                                self.sl.info("Attempting to click the radio button for %s", self.no_locator)
                                self.browser.execute_script("arguments[0].click()", noRadio)
                                self.sl.info("Clicked the radio button %s", self.no_locator)

                        else:
                            self.sl.warning("Unable to find question in my tiny database")

                    except Exception as e:
                        self.sl.exception("Could not answer additional questions: %s", e)
                        self.sl.error("Unable to submit due to error with no solution")
                        #return submitted
                attemptQuestions = False
                self.sl.info("no longer going to try and answer questions, since we have now tried")
            else:
                self.sl.error("Unable to submit due to error with no solution")
                #return submitted
        except Exception as e:
            self.sl.exception("Unable to answer questions")
            self.sl.error(e)

    def check_if_questions(self):
        self.sl.info("checking if questions..")
        if self.is_present(self.question_locator):
            self.sl.info("question exists")
            return True
        else:
            return False

    def check_if_upload(self):
        self.sl.info("checking if upload")
        if self.is_present(self.upload_locator):
            self.sl.info("upload exists")
            return True
        else:
            return False

    def upload(self):
        """
        This function is used to upload either a resume and/or cover letter.
        :arg
        """
        # if self.is_present(self.upload_locator):
        #    self.sl.info("Resume upload option available. Attempting to upload.")
        #     input_buttons = self.browser.find_elements(self.cover_letter[0],
        #                                                self.cover_letter[1])
        #     for input_button in input_buttons:
        #         parent = input_button.find_element(By.XPATH, "..")
        #         sibling = parent.find_element(By.XPATH, "preceding-sibling::*")
        #         grandparent = sibling.find_element(By.XPATH, "..")
        #         for key in self.uploads.keys():
        #             if key in sibling.text or key in grandparent.text:
        #                 input_button.send_keys(self.uploads[key])

        #TODO Should check if there is a class called "attachment-filename" b/c that means that a resume is already uploaded.
        # Should always upload new resume since you never know when a user may prefer their latest. Also, simply removing the
        # already uploaded resume will show you that you have a history of resumes that you have uploaded. The application will
        # automatically use the latest, regardless if you remove the previous one and push next button.
        try:
            self.sl.info("attempting to upload something")
            if self.is_present(self.upload_locator):
                self.sl.info("found upload button. clicking...")
                button = self.wait.until(EC.element_to_be_clickable(self.upload_locator))
                self.sl.info("Uploading resume now")
                time.sleep(random.uniform(2.2, 4.3))
                self.browser.execute_script("arguments[0].click()", button)
                # TODO This can only handle Chrome right now. Firefox or other browsers will need to be handled separately
                # Chrome opens the file browser window with the title "Open"
                status = wsh.AppActivate("Open")
                self.sl.debug("Able to find file browser diasl: %s", status)
                # Must sleep around sending the resume location so it has time to accept all keys submitted
                time.sleep(1)
                wsh.SendKeys(str(self.resume_loctn))
                time.sleep(1)
                wsh.SendKeys("{ENTER}")
                self.sl.info("Just finished using button %s ", self.upload_locator)
        except Exception as e:
            self.sl.exception("Unable to upload")
            self.sl.error(e)

    def determine_error(self):
        """
        This function is intended to detect errors on any screen, but as of August 7, 2020, it only detects errors related
        to the question states.
        :arg
        """
        #we should already know that the error element does exist, so go ahead and iterate through the locators.
        try:
            self.sl.info("Trying to determining possible error")
            for errorElement in self.browser.find_elements(self.error_locator[0],
                                                           self.error_locator[1]):
                text = errorElement.text
                #TODO This is not the best way to determine if there is an error, but it might be the only way
                # What if there is an error in another state? Just search for the text?
                if "Please enter a valid answer" in text:
                    self.sl.warning("Determined error; Current state should be the QUESTIONS state; Changing state")
                    self.to_QUESTIONS1()
                else:
                    self.sl.error("Unknown error. Should go to SUSPENDED state since there is no way to solve")
                    self.suspend()
        except Exception as e:
           self.sl.exception("Problem with determining error")
           self.sl.error(e)

    def check_for_error(self):
        try:
            self.sl.info("Checking for errors...")
            if self.is_present(self.error_locator):
                #error locator is often on element in the window but the error locator hidden element is only in the window
                #when there is potential for error, but not an error yet. If the hidden element does not exist, then but the
                #error element does, then there must be an error - return True in the else statement
                if self.is_present(self.error_locator_hidden):

                    #if there are multiple possibilities for errors on the page, then some could be hidden and others not
                    #if there are others that are not hidden, then we shold accept that there is possibly an error.
                    if self.is_present(self.error_locator_not_hidden):
                        self.sl.warning("Error detected")
                        # self.find_fail()
                        return True

                    self.sl.warning("No errors found")
                    return False
                else:
                    self.sl.warning("Error detected")
                    #self.find_fail()
                    return True
            else:
                return False
        except Exception as e:
            self.sl.exception("Problem with checking for errors")
            self.sl.error(e)

    def go_to_next(self):

        self.sl.info("Looking for NEXT button")
        if self.is_present(self.next_locator):
            try:
                self.sl.info("Next button found. Clicking...")
                next_button = self.wait.until(EC.element_to_be_clickable(self.next_locator))
                next_button.click()
                return True
            except Exception as e:
                self.sl.exception("Unable to click next button from TBD state to TBD state")
                return False
        else:
            return False

    def go_to_review(self):
        self.sl.info("Attempting to go to the REVIEW state")

        if self.is_present(self.review_locator):
            try:
                self.sl.info("Review button found. Clicking..")
                review_button = self.wait.until(EC.element_to_be_clickable(self.review_locator))
                review_button.click()
#                errorFound = self.check_for_error()
                return True
            except Exception as e:
                self.sl.exception("Unable to click review button from TBD state to TBD state")
                return False
        else:
            return False

    def submit_app(self):
        try:
            #if self.is_present(self.submit_locator):
            button = self.wait.until(EC.element_to_be_clickable(self.submit_locator))
            self.sl.info("attempting to click button: %s", str(self.submit_locator))
            response = button.click()
            self.sl.info("Clicked the submit button.")
            submitted = True
            return submitted
        except EC.StaleElementReferenceException:
            self.sl.error("Button was stale. Couldnt click")
        except Exception as e:
            self.sl.exception("Unable to submit app")
            self.sl.error(e)


    def go_to_submit(self):
        if self.is_present(self.submit_locator):
            try:
                submit_button = self.wait.until(EC.element_to_be_clickable(self.submit_locator))
                submit_button.click()
                return True
            except Exception as e:
                self.sl.exception("Unable to click submit button from TBD state to TBD state")
                return False
        else:
            return False

    def shout_suspension(self):
        self.sl.warning("In Suspension state")

    def go_to_upload(self):
        if self.is_present(self.upload_locator):
            try:
                upload_button = self.wait.until(EC.element_to_be_clickable(self.upload_locator))
                upload_button.click()
                return True
            except Exception as e:
                self.sl.exception("Unable to click upload button from TBD state to TBD state")
                return False
        else:
            return False

    def app_sleep(self):
        try:
            self.sl.info("Going to sleep for a little bit... (u.u) zZzZ")
            time.sleep(random.uniform(4.1, 6.6))
        except Exception as e:
            self.sl.exception("Unable to sleep")
            self.sl.error(e)


if __name__ == '__main__':
    browser = webdriver.Chrome()
    app = Application("myApp", browser)
    print(app.state)
    print("test")